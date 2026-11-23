"""MiniToo Bluetooth SPP packet framing and JPEG payload encoding.

Extracted from the working project; see docs/PROTOCOL.md.
"""
import io

CHUNK_SIZE = 256
CMD_EXTERN_CMD = 189
CMD_APP_NEW_GIF_CMD2020 = 139

def build_packet(command: int, data: bytes = b"") -> bytes:
    """Build a Divoom SPP protocol packet (from s.java method k())."""
    total = 1 + 2 + 1 + len(data) + 2 + 1  # header + len(2) + cmd + data + chk(2) + footer
    length_field = total - 4  # = len(data) + 3

    packet = bytearray(total)
    packet[0] = 0x01
    packet[1] = length_field & 0xFF
    packet[2] = (length_field >> 8) & 0xFF
    packet[3] = command & 0xFF

    for i, b in enumerate(data):
        packet[4 + i] = b

    checksum = 0
    for i in range(1, total - 2):
        checksum = (checksum + (packet[i] & 0xFF)) & 0xFFFF

    packet[-3] = checksum & 0xFF
    packet[-2] = (checksum >> 8) & 0xFF
    packet[-1] = 0x02

    return bytes(packet)


def build_ext_packet(ext_cmd: int, data: bytes = b"") -> bytes:
    """Build an extended command packet (wrapped in CMD_EXTERN_CMD)."""
    return build_packet(CMD_EXTERN_CMD, bytes([ext_cmd]) + data)


def hex_dump(data: bytes, max_bytes: int = 64) -> str:
    """Hex dump with optional truncation for large payloads."""
    if len(data) <= max_bytes:
        return " ".join(f"{b:02x}" for b in data)
    shown = " ".join(f"{b:02x}" for b in data[:max_bytes])
    return f"{shown} ... ({len(data)} bytes total)"


# ============================================================
# I/O helpers
# ============================================================

def encode_image_payload(img, jpeg_quality: int = 85) -> bytes:
    """
    Encode a PIL Image for the MiniToo wire protocol.

    From decompiled f3/c.java method r() and n3/h.java:
    - Payload header: [0x23, frame_count, speed_hi, speed_lo, row_cnt, col_cnt]
    - Frame: [encode_type, length_4B_BE, compressed_data...]

    The app uses LZO (native NDK) with JPEG as fallback. We go straight to JPEG
    since we don't have the NDK library. JPEG is encode_type 0x01.

    Image is encoded as 160x128 (landscape) — the device rotates for its
    portrait display. This matches f3.c.r() which checks is160X128().
    """
    from PIL import Image

    # Resize to 160x128 (encoding dimensions, device rotates to portrait)
    if img.size != (160, 128):
        img = img.resize((160, 128), Image.LANCZOS)
    img = img.convert("RGB")

    # JPEG encode
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=jpeg_quality)
    jpeg_data = buf.getvalue()

    # Payload header (from f3.c.r())
    # Format: [marker, frame_count, speed_hi, speed_lo, tile_rows, tile_cols]
    # TILE GRID, not pixel dimensions! 160px ÷ 20px/tile = 8 rows, 128px ÷ ~13px/tile = 10 cols
    header = bytes([
        0x23,        # frame marker for LCD mode
        0x01,        # frame count = 1 (single static image)
        0x00, 0x00,  # speed (0 for static, high byte first)
        0x08,        # tile rows = 8 (160 ÷ 20)
        0x0A,        # tile cols = 10 (128 ÷ ~13)
    ])

    # Frame: encode_type(1B) + data_length(4B BE) + data
    frame = bytes([0x01])  # 0x01 = JPEG encoding
    frame += len(jpeg_data).to_bytes(4, "big")
    frame += jpeg_data

    return header + frame


def build_chunks(payload: bytes) -> list[bytes]:
    """
    Split encoded payload into protocol packets, each carrying up to 256 bytes.

    Each chunk's data (before protocol wrapping):
      [0x01] + total_payload_len(4B LE) + chunk_index(2B LE) + chunk_bytes

    From n3/h.java method f(), lines 160-193:
    - prefix = [0x01] (set by l() in CmdManager.n() line 1526)
    - total length as 4 bytes LE (i9=4 for DevicePixel128)
    - chunk index as 2 bytes LE (i11=2 for DevicePixel128)
    - chunk data
    Each is wrapped in a standard protocol packet with command 0x8B.
    """
    chunks = []
    total_len = len(payload)
    num_chunks = (total_len + CHUNK_SIZE - 1) // CHUNK_SIZE

    for i in range(num_chunks):
        offset = i * CHUNK_SIZE
        chunk_data = payload[offset:offset + CHUNK_SIZE]

        # Chunk inner data: prefix + total_len + chunk_idx + data
        inner = bytes([0x01])                          # prefix marker
        inner += total_len.to_bytes(4, "little")       # total payload length
        inner += i.to_bytes(2, "little")               # chunk index
        inner += chunk_data                            # actual data

        chunks.append(build_packet(CMD_APP_NEW_GIF_CMD2020, inner))

    return chunks
