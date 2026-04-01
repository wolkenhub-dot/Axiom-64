import os
import sys
import struct
import argparse
import io
import hashlib
import concurrent.futures
import lzma
from typing import List, Tuple, Dict, Any

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
    return out

def delta_decode(data: bytes) -> bytearray:
    """Reversão do Filtro Delta"""
    length = len(data)
    out = bytearray(length)
    q = length // 4
    m = memoryview(data)
    out[0::4] = m[0:q]
    out[1::4] = m[q:2*q]
    out[2::4] = m[2*q:3*q]
    out[3::4] = m[3*q:length]
    return out

import math

def try_encode_webp(uncomp_data: bytearray) -> bytes:
    """Tenta converter textura raw descomprimida para WebP Lossless"""
    if not HAS_PIL:
        return None
    size = len(uncomp_data)
    if size < 2048:
        return None
        
    if size % 4 == 0:
        pixels = size // 4
        # Tenta adivinhar se é uma textura quadrada perfeita
        w = int(math.sqrt(pixels))
        if w * w == pixels:
            try:
                img = Image.frombytes('RGBA', (w, w), uncomp_data)
                buf = io.BytesIO()
                img.save(buf, format='webp', lossless=True)
                webp_data = buf.getvalue()
                # Exige uma economia de pelo menos 30% para valer a pena
                if len(webp_data) < size * 0.7:
                    return webp_data
            except Exception:
                pass
    return None

def worker_scan(full_data: bytes, start: int, end: int) -> List[Dict]:
    """Worker paralelo para caça de MIO0 e Yay0 e WebP conversão"""
    results = []
    pos = start
    while pos < end:
        magic = full_data[pos:pos+4]
        is_mio = (magic == b'MIO0')
        is_yay = (magic == b'Yay0')
        
        if is_mio or is_yay:
            try:
                if is_mio:
                    uncomp, comp_size = Mio0Codec.decode(full_data, pos)
                    ctype = b'M'
                else:
                    uncomp, comp_size = Yay0Codec.decode(full_data, pos)
                    ctype = b'Y'
                    
                webp_data = try_encode_webp(uncomp)
                if webp_data:
                    final_data = webp_data
                    ctype = b'W' if is_mio else b'X'
                else:
                    final_data = uncomp
                    
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
    """Worker paralelo para compressão LZMA"""
    data_to_compress = delta_encode(data) if is_delta else data
    comp = lzma.compress(data_to_compress, preset=9 | lzma.PRESET_EXTREME)
    return (chunk_id, is_delta, comp, len(data_to_compress))

def worker_decompress(chunk_id: int, compressed_data: bytes, uncompressed_size: int, is_delta: bool) -> Tuple[int, bytes]:
    """Worker paralelo para descompressão LZMA"""
    data = lzma.decompress(compressed_data)
    if is_delta:
        data = delta_decode(data)
    return (chunk_id, data)

def worker_reconstruct(offset: int, comp_size: int, ctype: bytes, chunk2_data: bytes) -> Tuple[int, int, bytes]:
    """Worker paralelo para desfazer texturas WebP e recomprimir LZ77 MIO0/Yay0"""
    try:
        if ctype in [b'W', b'X']:
            if not HAS_PIL:
                raise Exception("Biblioteca Pillow ausente para decodificar textura WebP.")
            buf = io.BytesIO(chunk2_data)
            img = Image.open(buf)
            uncomp_data = img.tobytes()
        else:
            uncomp_data = chunk2_data
            
        if ctype in [b'M', b'W']:
            recomp_block = Mio0Codec.encode(uncomp_data, original_compressed_size=comp_size)
        else:
            recomp_block = Yay0Codec.encode(uncomp_data, original_compressed_size=comp_size)
            
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
            limit = min(pos, 4096)
            for offset in range(1, limit + 1):
                match_len = 0
                while match_len < max_len and data[pos - offset + match_len] == data[pos + match_len]:
                    match_len += 1
                if match_len > best_len:
                    best_len = match_len
                    best_offset = offset
                    if best_len == 18: break

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
        block = header + layout_bytes + comp_data + uncomp_data
        
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
            limit = min(pos, 4096)
            for offset in range(1, limit + 1):
                match_len = 0
                while match_len < max_len and data[pos - offset + match_len] == data[pos + match_len]:
                    match_len += 1
                if match_len > best_len:
                    best_len = match_len
                    best_offset = offset
                    if best_len == max_len: break

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
        block = header + layout_bytes + link_data + nonlink_data

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
        
        print("[INFO] Escaneando ROM usando multiprocessing (ProcessPoolExecutor)...")
        num_workers = os.cpu_count() or 4
        stride = max(1024, (ROM_SIZE - bootcode_size) // num_workers)
        
        futures = []
        with concurrent.futures.ProcessPoolExecutor(max_workers=num_workers) as executor:
            for i in range(bootcode_size, ROM_SIZE, stride):
                end_pos = min(i + stride, ROM_SIZE)
                futures.append(executor.submit(worker_scan, self.data, i, end_pos))
                
            all_blocks = []
            for future in concurrent.futures.as_completed(futures):
                all_blocks.extend(future.result())
                
        # Filtra falsos positivos e overlays
        all_blocks.sort(key=lambda x: x['offset'])
        valid_blocks = []
        last_end = -1
        for b in all_blocks:
            if b['offset'] >= last_end:
                valid_blocks.append(b)
                last_end = b['offset'] + b['comp_size']
        
        raw_start = bootcode_size
        cw, cx = 0, 0
        for b in valid_blocks:
            if b['type'] == b'W': cw += 1
            if b['type'] == b'X': cx += 1
            
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
        print(f"       => {cw+cx} texturas comprimidas em WebP geradas pelo transcodificador.")
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
    def compress_rom(cls, input_file: str, output_file: str):
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
            z_futures.append(executor.submit(worker_compress, 1, chunker.chunk1_header, True, 22))
            z_futures.append(executor.submit(worker_compress, 2, chunker.chunk2_uncompressed, False, 22))
            # DESATIVANDO o Delta no Chunk 3 (Raw Data) para não destruir compressão de áudio
            z_futures.append(executor.submit(worker_compress, 3, chunker.chunk3_raw, False, 22))
            
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
    def extract_rom(cls, input_file: str, output_file: str):
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
                    final_rom[offset:offset+csize] = block

        # Juntando o RAW (Chunk 3) e os blocos reconstruídos
        # Como iteramos linearmente e o mapa já foi montado durante a compressão ordernadado logicamente
        raw_pos = 0
        curr_offset = len(chunk1)
        for entry in recon_map:
            raw_len = entry['offset'] - curr_offset
            if raw_len > 0:
                final_rom[curr_offset:curr_offset+raw_len] = chunk3[raw_pos:raw_pos+raw_len]
                raw_pos += raw_len
            curr_offset = entry['offset'] + entry['comp_size']
            
        if raw_pos < len(chunk3) and curr_offset < original_size:
            rem_len = min(len(chunk3) - raw_pos, original_size - curr_offset)
            final_rom[curr_offset:curr_offset+rem_len] = chunk3[raw_pos:raw_pos+rem_len]

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
    parser.add_argument("-c", "--compress", metavar="IN.z64", help="Comprime uma ROM para o formato .n64z")
    parser.add_argument("-x", "--extract", metavar="IN.n64z", help="Extrai um arquivo .n64z para .z64")
    parser.add_argument("-o", "--output", metavar="OUT", help="Arquivo de saída (Opcional)")

    args = parser.parse_args()

    if args.compress:
        input_f = args.compress
        output_f = args.output if args.output else input_f + ".n64z"
        N64ZContainer.compress_rom(input_f, output_f)
    elif args.extract:
        input_f = args.extract
        output_f = args.output if args.output else input_f.replace(".n64z", "") + "_rec.z64"
        N64ZContainer.extract_rom(input_f, output_f)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
