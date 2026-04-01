import os
import sys
import struct
import argparse
import io
import hashlib
import concurrent.futures
import lzma
import contextlib
import glob
from typing import List, Tuple, Dict, Any

@contextlib.contextmanager
def suppress_stdout():
    with open(os.devnull, "w") as devnull:
        old_stdout = sys.stdout
        sys.stdout = devnull
        try:  
            yield
        finally:
            sys.stdout = old_stdout

try:
    import zstandard as zstd
except ImportError:
    print("Erro: A biblioteca 'zstandard' não está instalada. Execute: pip install zstandard")
    sys.exit(1)

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    from tqdm import tqdm
except ImportError:
    print("Erro: A biblioteca 'tqdm' não está instalada. Execute: pip install tqdm")
    sys.exit(1)


# ==========================================
# Funções Globais e Workers (Multiprocessing)
# ==========================================

def delta_encode(data: bytes) -> bytearray:
    """Filtro Delta: Agrupa bytes de 32-bits MIPS para quebrar entropia"""
    length = len(data)
    padded_len = length + ((4 - length % 4) % 4)
    if length != padded_len:
        data = data.ljust(padded_len, b'\0')
        length = padded_len
    
    out = bytearray(length)
    q = length // 4
    m = memoryview(data)
    out[0:q] = m[0::4]
    out[q:2*q] = m[1::4]
    out[2*q:3*q] = m[2::4]
    out[3*q:length] = m[3::4]
    
    # Subtractive delta over transposed arrays bounds for intense 0x00 creation
    for block in range(4):
        start = block * q
        for i in range(start + q - 1, start, -1):
            out[i] = (out[i] - out[i-1]) & 0xFF
            
    return out

def delta_decode(data: bytes) -> bytearray:
    """Reversão do Filtro Delta"""
    length = len(data)
    out = bytearray(length)
    q = length // 4
    
    transposed = bytearray(data)
    for block in range(4):
        start = block * q
        for i in range(start + 1, start + q):
            transposed[i] = (transposed[i] + transposed[i-1]) & 0xFF
            
    m = memoryview(transposed)
    out[0::4] = m[0:q]
    out[1::4] = m[q:2*q]
    out[2::4] = m[2*q:3*q]
    out[3::4] = m[3*q:length]
    return out

import math

def try_encode_webp(uncomp_data: bytearray) -> Tuple[bytes, str]:
    """Tenta converter textura raw descomprimida para WebP Lossless adivinhando o formato 2D"""
    if not HAS_PIL:
        return None, None
    size = len(uncomp_data)
    # Ignora blocos muito pequenos ou grandes demais (maiores que 128KB não são texturas no N64)
    if size < 512 or size > 131072:
        return None, None
        
    import zlib
    if len(zlib.compress(uncomp_data, level=1)) > size * 0.90:
        # Se os dados não têm nem redundância básica 1D (alta entropia), WebP espacial vai falhar ou travar a CPU
        return None, None
        
    best_webp = None
    best_mode = None
    best_ratio = 0.85 # Exige uma economia de pelo menos 15% para valer a pena a estrutura extra
    valid_widths = [16, 32, 64] # Limitando matrizes pra evitar hang em código MIPS de alta entropia
    
    def test_compress(mode, w, h):
        nonlocal best_webp, best_ratio, best_mode
        try:
            img = Image.frombytes(mode, (w, h), uncomp_data)
            buf = io.BytesIO()
            # Fast check first
            img.save(buf, format='webp', lossless=True, quality=100, method=0)
            if len(buf.getvalue()) < size * 0.95:
                # Se for minimamente promissor espacialmente, aperta o cinto e comprime pra valer
                buf = io.BytesIO()
                img.save(buf, format='webp', lossless=True, quality=100, method=6, exact=True)
                webp_data = buf.getvalue()
                if len(webp_data) < size * best_ratio:
                    best_webp = webp_data
                    best_mode = mode
                    best_ratio = len(webp_data) / size
        except Exception: pass

    # Mode L (1 byte per pixel: I8/CI8/I4/CI4)
    for w in valid_widths:
        if size % w == 0:
            h = size // w
            if 4 <= h <= 1024:
                test_compress('L', w, h)
                
    # Mode LA/RGB16 (2 bytes per pixel: RGBA16 / IA16)
    if size % 2 == 0:
        pixels = size // 2
        for w in valid_widths:
            if pixels % w == 0:
                h = pixels // w
                if 4 <= h <= 1024:
                    test_compress('LA', w, h)

    # Mode RGBA (4 bytes per pixel: RGBA32)
    if size % 4 == 0:
        pixels = size // 4
        for w in valid_widths:
            if pixels % w == 0:
                h = pixels // w
                if 4 <= h <= 512:
                    test_compress('RGBA', w, h)
                    
    return best_webp, best_mode

def worker_scan(full_data: bytes, start: int, end: int) -> List[Dict]:
    """Worker paralelo para caça de MIO0 e Yay0 e WebP conversão"""
    results = []
    pos = start
    while pos < end:
        magic = full_data[pos:pos+4]
        is_mio = (magic == b'MIO0')
        is_yay = (magic == b'Yay0')
        is_yaz = (magic == b'Yaz0')
        
        if is_mio or is_yay or is_yaz:
            try:
                if is_mio:
                    uncomp, comp_size = Mio0Codec.decode(full_data, pos)
                    ctype = b'M'
                elif is_yay:
                    uncomp, comp_size = Yay0Codec.decode(full_data, pos)
                    ctype = b'Y'
                else:
                    uncomp, comp_size = Yaz0Codec.decode(full_data, pos)
                    ctype = b'Z'
                    
                webp_data, webp_mode = try_encode_webp(uncomp)
                # Aceitar WebP diretamente se for menor que os dados originais — sem re-encode Python!
                if webp_data and len(webp_data) < comp_size:
                    final_data = webp_data
                    if is_mio: ctype = b'1' if webp_mode == 'L' else (b'2' if webp_mode == 'LA' else b'3')
                    elif is_yay: ctype = b'4' if webp_mode == 'L' else (b'5' if webp_mode == 'LA' else b'6')
                    else: ctype = b'7' if webp_mode == 'L' else (b'8' if webp_mode == 'LA' else b'9')
                else:
                    final_data = full_data[pos : pos + comp_size]
                    
                results.append({
                    'offset': pos,
                    'comp_size': comp_size,
                    'uncomp_size': len(uncomp),
                    'chunk2_size': len(final_data),
                    'type': ctype,
                    'data': final_data
                })
                # Evitar detecções que estão dentro do payload do chunk atual
                pos += comp_size
                continue
            except Exception:
                pass
        pos += 4
    return results

def worker_compress(chunk_id: int, data: bytes, is_delta: bool, level: int) -> Tuple[int, bool, bytes, int]:
    """Worker paralelo para compressão LZMA com Delta Filter nativo em C"""
    if is_delta:
        # lzma.FILTER_DELTA dist=4 é 100% C, alinhado com MIPS 32-bit — sem custo Python
        lzma_filters = [
            {"id": lzma.FILTER_DELTA, "dist": 4},
            {"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME, "dict_size": 64 * 1024 * 1024, "lc": 3, "lp": 0, "pb": 2}
        ]
    else:
        lzma_filters = [
            {"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME, "dict_size": 64 * 1024 * 1024, "lc": 3, "lp": 0, "pb": 2}
        ]
    comp = lzma.compress(bytes(data), format=lzma.FORMAT_XZ, filters=lzma_filters)
    return (chunk_id, is_delta, comp, len(data))

def worker_decompress(chunk_id: int, compressed_data: bytes, uncompressed_size: int, is_delta: bool) -> Tuple[int, bytes]:
    """Worker paralelo para descompressão LZMA — delta é desfeito automaticamente pelo filtro C"""
    data = lzma.decompress(compressed_data)
    return (chunk_id, data)

def worker_reconstruct(offset: int, comp_size: int, ctype: bytes, chunk2_data: bytes) -> Tuple[int, int, bytes]:
    """Worker paralelo para desfazer texturas WebP e recomprimir LZ77 MIO0/Yay0"""
    try:
        is_webp = ctype in [b'1', b'2', b'3', b'4', b'5', b'6', b'7', b'8', b'9']
        
        if is_webp:
            if not HAS_PIL:
                raise Exception("Biblioteca Pillow ausente para decodificar textura WebP.")
            
            webp_mode = 'L' if ctype in [b'1', b'4', b'7'] else ('LA' if ctype in [b'2', b'5', b'8'] else 'RGBA')
            
            buf = io.BytesIO(chunk2_data)
            img = Image.open(buf)
            img = img.convert(webp_mode)
            uncomp_data = img.tobytes()
        else:
            uncomp_data = chunk2_data
            
        is_mio = ctype in [b'M', b'1', b'2', b'3']
        is_yay = ctype in [b'Y', b'4', b'5', b'6']
        is_yaz = ctype in [b'Z', b'7', b'8', b'9']
        
        if is_mio:
            if ctype == b'M': recomp_block = chunk2_data
            else: recomp_block = Mio0Codec.encode(uncomp_data, original_compressed_size=comp_size)
        elif is_yay:
            if ctype == b'Y': recomp_block = chunk2_data
            else: recomp_block = Yay0Codec.encode(uncomp_data, original_compressed_size=comp_size)
        else:
            if ctype == b'Z': recomp_block = chunk2_data
            else: recomp_block = Yaz0Codec.encode(uncomp_data, original_compressed_size=comp_size)
            
        if len(recomp_block) > comp_size:
            recomp_block = recomp_block[:comp_size]
            
        return (offset, comp_size, recomp_block)
    except Exception as e:
        print(f"Erro no worker de reconstrução Offset={offset}: {e}")
        return None


# ==========================================
# Compressores Legados Otimizados (MIO0/Yay0)
# ==========================================

class RomNormalizer:
    MAGIC_Z64 = 0x80371240
    MAGIC_V64 = 0x37804012
    MAGIC_N64 = 0x40123780

    @staticmethod
    def normalize(data: bytes) -> bytearray:
        if len(data) < 4:
            raise ValueError("Arquivo de entrada muito pequeno para ser uma ROM N64.")

        magic = struct.unpack(">I", data[:4])[0]
        out_data = bytearray(data)

        if magic == RomNormalizer.MAGIC_Z64:
            pass
        elif magic == RomNormalizer.MAGIC_V64:
            for i in range(0, len(out_data) - 1, 2):
                out_data[i], out_data[i + 1] = out_data[i + 1], out_data[i]
        elif magic == RomNormalizer.MAGIC_N64:
            for i in range(0, len(out_data) - 3, 4):
                out_data[i], out_data[i + 3] = out_data[i + 3], out_data[i]
                out_data[i + 1], out_data[i + 2] = out_data[i + 2], out_data[i + 1]
            
        return out_data

    @staticmethod
    def get_sha256(data: bytes) -> bytes:
        return hashlib.sha256(data).digest()


class Mio0Codec:
    @staticmethod
    def decode(data: bytes, offset: int) -> Tuple[bytearray, int]:
        magic, decomp_size, comp_offset, uncomp_offset = struct.unpack(">4sIII", data[offset:offset + 16])
        if magic != b'MIO0': raise ValueError("Not MIO0")
        
        out = bytearray()
        layout_offset = offset + 16
        layout_bit_idx = 0
        layout_byte = data[layout_offset]
        comp_pos = offset + comp_offset
        uncomp_pos = offset + uncomp_offset

        while len(out) < decomp_size:
            if layout_bit_idx == 8:
                layout_offset += 1
                layout_byte = data[layout_offset]
                layout_bit_idx = 0

            bit = (layout_byte >> (7 - layout_bit_idx)) & 1
            layout_bit_idx += 1

            if bit == 1:
                out.append(data[uncomp_pos])
                uncomp_pos += 1
            else:
                match_bytes = struct.unpack(">H", data[comp_pos:comp_pos + 2])[0]
                comp_pos += 2
                length = (match_bytes >> 12) + 3
                match_offset = (match_bytes & 0x0FFF) + 1
                start = len(out) - match_offset
                for i in range(length):
                    out.append(out[start + i])

        consumed = max(layout_offset + 1, comp_pos, uncomp_pos) - offset
        if consumed % 16 != 0: consumed += (16 - (consumed % 16))
        return out, consumed

    @staticmethod
    def encode(data: bytes, original_compressed_size: int = 0) -> bytes:
        layout_bits = []
        uncomp_data = bytearray()
        comp_data = bytearray()
        pos = 0
        size = len(data)

        while pos < size:
            best_len, best_offset = 0, 0
            max_len = min(18, size - pos)
            if max_len >= 3:
                limit_pos = max(0, pos - 4096)
                target = data[pos:pos+3]
                f_idx = data.rfind(target, limit_pos, pos + 2)
                if f_idx != -1 and f_idx < pos:
                    best_len = 3
                    best_offset = pos - f_idx
                    while best_len < max_len:
                        next_target = data[pos:pos+best_len+1]
                        next_idx = data.rfind(next_target, limit_pos, pos + best_len)
                        if next_idx != -1 and next_idx < pos:
                            best_len += 1
                            best_offset = pos - next_idx
                        else:
                            break

            if best_len >= 3:
                layout_bits.append(0)
                comp_data.extend(struct.pack(">H", ((best_len - 3) << 12) | (best_offset - 1)))
                pos += best_len
            else:
                layout_bits.append(1)
                uncomp_data.append(data[pos])
                pos += 1

        layout_bytes = bytearray()
        for i in range(0, len(layout_bits), 8):
            byte_val = 0
            for j in range(8):
                if i + j < len(layout_bits):
                    byte_val |= (layout_bits[i + j] << (7 - j))
            layout_bytes.append(byte_val)

        while len(layout_bytes) % 4 != 0: layout_bytes.append(0)
        
        comp_offset = 16 + len(layout_bytes)
        uncomp_offset = comp_offset + len(comp_data)
        header = struct.pack(">4sIII", b'MIO0', size, comp_offset, uncomp_offset)
        block = bytearray(header) + layout_bytes + comp_data + uncomp_data
        
        while len(block) % 16 != 0: block.append(0)
        if original_compressed_size > 0 and len(block) < original_compressed_size:
            block += b'\x00' * (original_compressed_size - len(block))
        return bytes(block)


class Yay0Codec:
    @staticmethod
    def decode(data: bytes, offset: int) -> Tuple[bytearray, int]:
        magic, decomp_size, link_offset, nonlink_offset = struct.unpack(">4sIII", data[offset:offset + 16])
        if magic != b'Yay0': raise ValueError("Not Yay0")

        out = bytearray()
        layout_offset = offset + 16
        layout_bit_idx = 0
        layout_byte = 0
        link_pos = offset + link_offset
        nonlink_pos = offset + nonlink_offset

        while len(out) < decomp_size:
            if layout_bit_idx == 0:
                layout_byte = data[layout_offset]
                layout_offset += 1
                layout_bit_idx = 8

            bit = (layout_byte >> 7) & 1
            layout_byte = (layout_byte << 1) & 0xFF
            layout_bit_idx -= 1

            if bit == 1:
                out.append(data[nonlink_pos])
                nonlink_pos += 1
            else:
                match_bytes = struct.unpack(">H", data[link_pos:link_pos + 2])[0]
                link_pos += 2
                length = match_bytes >> 12
                match_offset = (match_bytes & 0x0FFF) + 1
                if length == 0:
                    length = data[nonlink_pos] + 18
                    nonlink_pos += 1
                else: length += 2

                start = len(out) - match_offset
                for i in range(length):
                    out.append(out[start + i])

        consumed = max(layout_offset, link_pos, nonlink_pos) - offset
        if consumed % 16 != 0: consumed += (16 - (consumed % 16))
        return out, consumed

    @staticmethod
    def encode(data: bytes, original_compressed_size: int = 0) -> bytes:
        layout_bits = []
        nonlink_data = bytearray()
        link_data = bytearray()
        pos, size = 0, len(data)

        while pos < size:
            best_len, best_offset = 0, 0
            max_len = min(255 + 18, size - pos)
            if max_len >= 3:
                limit_pos = max(0, pos - 4096)
                target = data[pos:pos+3]
                f_idx = data.rfind(target, limit_pos, pos + 2)
                if f_idx != -1 and f_idx < pos:
                    best_len = 3
                    best_offset = pos - f_idx
                    while best_len < max_len:
                        next_target = data[pos:pos+best_len+1]
                        next_idx = data.rfind(next_target, limit_pos, pos + best_len)
                        if next_idx != -1 and next_idx < pos:
                            best_len += 1
                            best_offset = pos - next_idx
                        else:
                            break

            if best_len >= 3:
                layout_bits.append(0)
                if best_len <= 17:
                    link_data.extend(struct.pack(">H", ((best_len - 2) << 12) | (best_offset - 1)))
                else:
                    link_data.extend(struct.pack(">H", best_offset - 1))
                    nonlink_data.append(best_len - 18)
                pos += best_len
            else:
                layout_bits.append(1)
                nonlink_data.append(data[pos])
                pos += 1

        layout_bytes = bytearray()
        for i in range(0, len(layout_bits), 8):
            byte_val = 0
            for j in range(8):
                if i + j < len(layout_bits):
                    byte_val |= (layout_bits[i + j] << (7 - j))
            layout_bytes.append(byte_val)

        while len(layout_bytes) % 4 != 0: layout_bytes.append(0)

        link_offset = 16 + len(layout_bytes)
        nonlink_offset = link_offset + len(link_data)
        header = struct.pack(">4sIII", b'Yay0', size, link_offset, nonlink_offset)
        block = bytearray(header) + layout_bytes + link_data + nonlink_data

        while len(block) % 16 != 0: block.append(0)
        if original_compressed_size > 0 and len(block) < original_compressed_size:
            block += b'\x00' * (original_compressed_size - len(block))
        return bytes(block)


class Yaz0Codec:
    @staticmethod
    def decode(data: bytes, offset: int) -> Tuple[bytearray, int]:
        magic, decomp_size = struct.unpack(">4sI", data[offset:offset + 8])
        if magic != b'Yaz0': raise ValueError("Not Yaz0")

        out = bytearray()
        pos = offset + 16
        layout_bit_idx = 0
        layout_byte = 0

        while len(out) < decomp_size:
            if layout_bit_idx == 0:
                layout_byte = data[pos]
                pos += 1
                layout_bit_idx = 8

            bit = (layout_byte >> 7) & 1
            layout_byte = (layout_byte << 1) & 0xFF
            layout_bit_idx -= 1

            if bit == 1:
                out.append(data[pos])
                pos += 1
            else:
                match_bytes = struct.unpack(">H", data[pos:pos + 2])[0]
                pos += 2
                length = match_bytes >> 12
                match_offset = (match_bytes & 0x0FFF) + 1
                if length == 0:
                    length = data[pos] + 18
                    pos += 1
                else: length += 2

                start = len(out) - match_offset
                for i in range(length):
                    out.append(out[start + i])

        consumed = pos - offset
        if consumed % 16 != 0: consumed += (16 - (consumed % 16))
        return out, consumed

    @staticmethod
    def encode(data: bytes, original_compressed_size: int = 0) -> bytes:
        comp_data = bytearray()
        pos, size = 0, len(data)

        layout_byte = 0
        layout_bit = 7
        group_data = bytearray()
        
        while pos < size:
            best_len, best_offset = 0, 0
            max_len = min(255 + 18, size - pos)
            if max_len >= 3:
                limit_pos = max(0, pos - 4096)
                target = data[pos:pos+3]
                f_idx = data.rfind(target, limit_pos, pos + 2)
                if f_idx != -1 and f_idx < pos:
                    best_len = 3
                    best_offset = pos - f_idx
                    while best_len < max_len:
                        next_target = data[pos:pos+best_len+1]
                        next_idx = data.rfind(next_target, limit_pos, pos + best_len)
                        if next_idx != -1 and next_idx < pos:
                            best_len += 1
                            best_offset = pos - next_idx
                        else:
                            break

            if best_len >= 3:
                layout_byte &= ~(1 << layout_bit)
                if best_len <= 17:
                    group_data.extend(struct.pack(">H", ((best_len - 2) << 12) | (best_offset - 1)))
                else:
                    group_data.extend(struct.pack(">H", best_offset - 1))
                    group_data.append(best_len - 18)
                pos += best_len
            else:
                layout_byte |= (1 << layout_bit)
                group_data.append(data[pos])
                pos += 1

            layout_bit -= 1
            if layout_bit < 0 or pos >= size:
                comp_data.append(layout_byte)
                comp_data.extend(group_data)
                layout_byte = 0
                layout_bit = 7
                group_data.clear()

        header = struct.pack(">4sIII", b'Yaz0', size, 0, 0)
        block = bytearray(header) + comp_data

        while len(block) % 16 != 0: block.append(0)
        if original_compressed_size > 0 and len(block) < original_compressed_size:
            block += b'\x00' * (original_compressed_size - len(block))
        return bytes(block)


# ==========================================
# Chunker e Container Principal
# ==========================================

class DataChunker:
    def __init__(self, data: bytearray):
        self.data = data
        self.chunk1_header = bytearray()
        self.chunk2_uncompressed = bytearray()
        self.chunk3_raw = bytearray()
        self.reconstruction_map = []

    def scan_and_chunk(self):
        ROM_SIZE = len(self.data)
        bootcode_size = min(4096, ROM_SIZE)
        self.chunk1_header = self.data[:bootcode_size]
        
        print("[INFO] Escaneando ROM (scan linear in-process)...")
        # Scan linear: ProcessPoolExecutor picklaria 32MB p/ cada subprocess — lento demais!
        all_blocks = worker_scan(self.data, bootcode_size, ROM_SIZE)
                
        # Filtra falsos positivos e overlays
        all_blocks.sort(key=lambda x: x['offset'])
        valid_blocks = []
        last_end = -1
        for b in all_blocks:
            if b['offset'] >= last_end:
                valid_blocks.append(b)
                last_end = b['offset'] + b['comp_size']
        
        raw_start = bootcode_size
        cw, cx, cv = 0, 0, 0
        for b in valid_blocks:
            if b['type'] == b'W': cw += 1
            if b['type'] == b'X': cx += 1
            if b['type'] == b'V': cv += 1
            
            if b['offset'] > raw_start:
                self.chunk3_raw.extend(self.data[raw_start:b['offset']])
                
            self.chunk2_uncompressed.extend(b['data'])
            
            self.reconstruction_map.append({
                'offset': b['offset'],
                'comp_size': b['comp_size'],
                'uncomp_size': b['uncomp_size'],
                'chunk2_size': b['chunk2_size'],
                'type': b['type']
            })
            raw_start = b['offset'] + b['comp_size']
            
        if raw_start < ROM_SIZE:
            self.chunk3_raw.extend(self.data[raw_start:])
            
        print(f"[INFO] Escaneamento finalizado ({len(valid_blocks)} blocos isolados).")
        print(f"       => {cw+cx+cv} texturas comprimidas em WebP geradas pelo transcodificador.")
        print(f"       Chunk 1 (Bootcode): {len(self.chunk1_header)} bytes")
        print(f"       Chunk 2 (Descomp. + WebP): {len(self.chunk2_uncompressed)} bytes")
        print(f"       Chunk 3 (Raw Delta MIPS): {len(self.chunk3_raw)} bytes")


class N64ZContainer:
    MAGIC = b'N64Z'
    VERSION = 2  # v2 with Delta & WebP support

    @staticmethod
    def _pack_chunk(chunk_id: int, flags: int, compressed: bytes, uncompressed_size: int) -> bytes:
        header = struct.pack(">BBII", chunk_id, flags, len(compressed), uncompressed_size)
        return header + compressed

    @classmethod
    def compress_rom(cls, input_file: str, output_file: str, quiet: bool = False):
        if quiet:
            with suppress_stdout():
                cls._compress_rom_inner(input_file, output_file)
        else:
            cls._compress_rom_inner(input_file, output_file)

    @classmethod
    def _compress_rom_inner(cls, input_file: str, output_file: str):
        print(f"=== Iniciando N64-URC GOD TIER ===")
        print(f"Lendo {input_file}...")
        with open(input_file, "rb") as f:
            raw_data = f.read()

        norm_data = RomNormalizer.normalize(raw_data)
        original_sha256 = RomNormalizer.get_sha256(norm_data)
        original_size = len(norm_data)
        print(f"[INFO] SHA-256 Original: {original_sha256.hex()}")

        chunker = DataChunker(norm_data)
        chunker.scan_and_chunk()

        print("[INFO] Comprimindo chunks em multiprocessamento com Delta Filter...")
        z_futures = []
        with concurrent.futures.ProcessPoolExecutor(max_workers=3) as executor:
            # Chunk 1 e 3 sofrem byte shuffling (MIPS 32-bits)
            # Apenas o Header/Bootcode (Chunk 1) costuma ter muito código MIPS puro
            # Chunk1 (bootcode MIPS) e Chunk3 (raw MIPS) usam FILTER_DELTA dist=4 em C puro
            z_futures.append(executor.submit(worker_compress, 1, chunker.chunk1_header, True, 22))
            # Chunk2 (texturas WebP + dados descomprimidos) — sem delta para preservar dados visuais
            z_futures.append(executor.submit(worker_compress, 2, chunker.chunk2_uncompressed, False, 22))
            # Chunk3 (raw data: MIPS code, audio, geometry) — delta dist=4 colapsa ponteiros à zero
            z_futures.append(executor.submit(worker_compress, 3, chunker.chunk3_raw, True, 22))
            
            results = {1: None, 2: None, 3: None}
            for future in concurrent.futures.as_completed(z_futures):
                cid, is_delta, comp, usize = future.result()
                results[cid] = (is_delta, comp, usize)
                
        c_chunks = bytearray()
        for i in range(1, 4):
            is_delta, comp, usize = results[i]
            flags = 0x01 if is_delta else 0x00
            c_chunks.extend(cls._pack_chunk(i, flags, comp, usize))

        map_bytes = bytearray()
        map_bytes.extend(struct.pack(">I", len(chunker.reconstruction_map)))
        for entry in chunker.reconstruction_map:
            # 17 bytes per struct map item
            map_bytes.extend(struct.pack(">IIII1s", entry['offset'], entry['comp_size'], entry['uncomp_size'], entry['chunk2_size'], entry['type']))

        print("[INFO] Montando Container .n64z God Tier...")
        with open(output_file, "wb") as f:
            f.write(struct.pack(">4sB32sIH", cls.MAGIC, cls.VERSION, original_sha256, original_size, 3))
            f.write(struct.pack(">I", len(map_bytes)))
            f.write(map_bytes)
            f.write(c_chunks)

        print(f"=== Compressão Concluída: {output_file} ===")
        print(f"    Tamanho original: {original_size} bytes")
        print(f"    Tamanho final: {os.path.getsize(output_file)} bytes")


    @classmethod
    def extract_rom(cls, input_file: str, output_file: str, quiet: bool = False):
        if quiet:
            with suppress_stdout():
                cls._extract_rom_inner(input_file, output_file)
        else:
            cls._extract_rom_inner(input_file, output_file)

    @classmethod
    def _extract_rom_inner(cls, input_file: str, output_file: str):
        print(f"=== Extraindo God Tier .n64z (N64-URC) ===")
        print(f"Lendo {input_file}...")
        with open(input_file, "rb") as f:
            data = f.read()

        magic, version, original_sha256, original_size, chunks_count = struct.unpack(">4sB32sIH", data[:43])
        if magic != cls.MAGIC:
            raise ValueError("Arquivo inválido (Magia do formato incorreta)")

        pos = 43
        map_length = struct.unpack(">I", data[pos:pos+4])[0]
        pos += 4
        map_data = data[pos:pos+map_length]
        pos += map_length
        
        recon_map = []
        map_entries = struct.unpack(">I", map_data[:4])[0]
        m_pos = 4
        for _ in range(map_entries):
            r_offset, r_csize, r_usize, r_chunk2size, r_type = struct.unpack(">IIII1s", map_data[m_pos:m_pos+17])
            m_pos += 17
            recon_map.append({
                'offset': r_offset, 'comp_size': r_csize, 'uncomp_size': r_usize,
                'chunk2_size': r_chunk2size, 'type': r_type
            })

        print(f"[INFO] Descomprimindo os {chunks_count} chunks em paralelo...")
        d_futures = []
        with concurrent.futures.ProcessPoolExecutor(max_workers=chunks_count) as executor:
            for _ in range(chunks_count):
                chunk_id, flags, comp_size, uncomp_size = struct.unpack(">BBII", data[pos:pos+10])
                pos += 10
                c_data = data[pos:pos+comp_size]
                pos += comp_size
                is_delta = (flags & 0x01) == 0x01
                d_futures.append(executor.submit(worker_decompress, chunk_id, c_data, uncomp_size, is_delta))
                
            chunks = {}
            for future in concurrent.futures.as_completed(d_futures):
                cid, udata = future.result()
                chunks[cid] = udata

        chunk1 = chunks.get(1, b'')
        chunk2 = bytearray(chunks.get(2, b''))
        chunk3 = chunks.get(3, b'')

        print(f"[INFO] Reconstruindo ROM em multiprocessamento: WebP -> Descomp -> LZ77 -> Binário...")
        final_rom = bytearray(original_size)
        final_rom[:len(chunk1)] = chunk1

        recon_futures = []
        u_pos = 0
        num_workers = os.cpu_count() or 4
        with concurrent.futures.ProcessPoolExecutor(max_workers=num_workers) as executor:
            for entry in recon_map:
                c2_data = chunk2[u_pos:u_pos+entry['chunk2_size']]
                u_pos += entry['chunk2_size']
                recon_futures.append(executor.submit(worker_reconstruct, entry['offset'], entry['comp_size'], entry['type'], c2_data))

            for future in concurrent.futures.as_completed(recon_futures):
                res = future.result()
                if res:
                    offset, csize, block = res
                    if len(block) != csize:
                        block = block.ljust(csize, b'\x00')[:csize]
                    final_rom[offset:offset+csize] = block

        # Juntando o RAW (Chunk 3) e os blocos reconstruídos
        # Como iteramos linearmente e o mapa já foi montado durante a compressão ordernadado logicamente
        raw_pos = 0
        curr_offset = len(chunk1)
        for entry in recon_map:
            raw_len = entry['offset'] - curr_offset
            if raw_len > 0:
                slice_data = chunk3[raw_pos:raw_pos+raw_len]
                if len(slice_data) != raw_len:
                    slice_data = slice_data.ljust(raw_len, b'\x00')[:raw_len]
                final_rom[curr_offset:curr_offset+raw_len] = slice_data
                raw_pos += raw_len
            curr_offset = entry['offset'] + entry['comp_size']
            
        if raw_pos < len(chunk3) and curr_offset < original_size:
            rem_len = min(len(chunk3) - raw_pos, original_size - curr_offset)
            slice_data = chunk3[raw_pos:raw_pos+rem_len]
            if len(slice_data) != rem_len:
                slice_data = slice_data.ljust(rem_len, b'\x00')[:rem_len]
            final_rom[curr_offset:curr_offset+rem_len] = slice_data

        new_sha256 = hashlib.sha256(final_rom).digest()
        print(f"[INFO] SHA-256 Esperado: {original_sha256.hex()}")
        print(f"[INFO] SHA-256 Obtido  : {new_sha256.hex()}")

        if new_sha256 != original_sha256:
            print("[ALERTA CRÍTICO] O SHA-256 diferiu! Possível limitação algorítmica ou falta da lib Pillow.")
        else:
            print("[SUCESSO] Hash SHA-256 corresponde perfeitamente!")

        with open(output_file, "wb") as f:
            f.write(final_rom)
        print(f"=== Extração concluída: {output_file} ===")


def main():
    parser = argparse.ArgumentParser(description="N64-URC God Tier: Nintendo 64 Ultimate ROM Compressor")
    parser.add_argument("-c", "--compress", nargs='+', metavar="IN.z64", help="Comprime ROMs para o formato .n64z")
    parser.add_argument("-x", "--extract", nargs='+', metavar="IN.n64z", help="Extrai arquivos .n64z para .z64")
    parser.add_argument("-o", "--output", metavar="OUT", help="Arquivo de saída (Apenas para arquivo único)")

    args = parser.parse_args()

    mode = None
    raw_files = []
    if args.compress:
        mode = 'compress'
        raw_files = args.compress
    elif args.extract:
        mode = 'extract'
        raw_files = args.extract
    else:
        parser.print_help()
        return

    expanded_files = []
    for pattern in raw_files:
        if '*' in pattern or '?' in pattern:
            expanded_files.extend(glob.glob(pattern))
        else:
            expanded_files.append(pattern)

    if mode == 'compress':
        files = [f for f in expanded_files if f.lower().endswith(('.z64', '.n64', '.v64', '.rom'))]
    else:
        files = [f for f in expanded_files if f.lower().endswith('.n64z')]

    if not files:
        print("[ERRO] Nenhum arquivo válido encontrado para processamento.")
        return

    is_batch = len(files) > 1
    if is_batch and args.output:
        print("[AVISO] Arquivo de saída customizado (-o) ignorado. Modo Batch usa nomenclatura automática.")
        args.output = None

    iterator = tqdm(files, desc="Processando Fullset", unit="ROM") if is_batch else files

    for f in iterator:
        if is_batch:
            iterator.set_postfix(file=os.path.basename(f))
            
        out_file = args.output if not is_batch and args.output else None
        
        if mode == 'compress':
            N64ZContainer.compress_rom(f, out_file or f + ".n64z", quiet=is_batch)
        else:
            final_out = out_file or f.replace(".n64z", "")
            if not final_out.lower().endswith(('.z64', '.n64', '.v64', '.rom')):
                final_out += ".z64"
            N64ZContainer.extract_rom(f, final_out, quiet=is_batch)

if __name__ == "__main__":
    main()
