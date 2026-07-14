"""
CucumberVPet .cvpkg Decryptor
Reverse-engineered from CucumberVPet.dll v1.1.4 (SevenVoxel, .NET 8.0)

Format: CVPKG1(6B) + salt(16B) + nonce(12B) + tag(16B) + ciphertext(AES-GCM)
Key: PBKDF2-SHA256(password, salt_prefix + file[6:22], 120000, 32)
"""

import sys
import io
import zipfile
import hashlib
import os
import argparse
from pathlib import Path

# --- Constants (from CucumberVPet.dll) ---
PACKAGE_PASSWORD = "SevenVoxel.CucumberVPet.RolePackage.v1"
KEY_SALT_PREFIX = b"CucumberVPet.PackageKey.v1"  # UTF-8
PBKDF2_ITERATIONS = 120000
PBKDF2_DKLEN = 32
HEADER = b"CVPKG1"


def _pbkdf2_sha256(password_bytes: bytes, salt: bytes, iterations: int, dklen: int) -> bytes:
    """PBKDF2 with SHA256 using hashlib (stdlib, no cryptography dep)."""
    return hashlib.pbkdf2_hmac("sha256", password_bytes, salt, iterations, dklen)


def decrypt_cvpkg(filepath: str | Path) -> bytes:
    """Decrypt a .cvpkg file and return the ZIP bytes."""
    filepath = Path(filepath)
    data = filepath.read_bytes()

    if len(data) < 50:
        raise ValueError(f"File too small: {len(data)} bytes (need >= 50)")

    if data[:6] != HEADER:
        raise ValueError(f"Invalid header: expected {HEADER!r}, got {data[:6]!r}")

    salt = data[6:22]       # 16 bytes
    nonce = data[22:34]     # 12 bytes
    tag = data[34:50]       # 16 bytes
    ciphertext = data[50:]

    # Derive key
    key_salt = KEY_SALT_PREFIX + salt
    password_bytes = PACKAGE_PASSWORD.encode("utf-8")
    key = _pbkdf2_sha256(password_bytes, key_salt, PBKDF2_ITERATIONS, PBKDF2_DKLEN)

    # AES-GCM decrypt
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(nonce, ciphertext + tag, None)
    except ImportError:
        raise ImportError(
            "cryptography library required. Install: pip install cryptography"
        )

    # Verify it's a valid ZIP
    if plaintext[:4] != b"PK\x03\x04":
        raise ValueError(
            f"Decryption succeeded but not a valid ZIP. "
            f"First bytes: {plaintext[:16].hex()}"
        )

    return plaintext


def extract_cvpkg(filepath: str | Path, output_dir: str | Path) -> Path:
    """Decrypt and extract a .cvpkg file to output_dir."""
    filepath = Path(filepath)
    output_dir = Path(output_dir)

    print(f"Decrypting: {filepath.name} ({filepath.stat().st_size / 1_000_000:.1f} MB)")
    zip_data = decrypt_cvpkg(filepath)

    output_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        names = zf.namelist()
        print(f"  Extracting {len(names)} files to {output_dir}")
        zf.extractall(output_dir)

        # Count model files
        model3 = [n for n in names if n.endswith(".model3.json")]
        moc3 = [n for n in names if n.endswith(".moc3")]
        motions = [n for n in names if ".motion3.json" in n]
        exps = [n for n in names if ".exp3.json" in n]

        if model3:
            print(f"  Model: {model3[0]}")
        if moc3:
            print(f"  Moc3: {moc3[0]}")
        print(f"  Motions: {len(motions)}, Expressions: {len(exps)}")

    return output_dir


def main():
    parser = argparse.ArgumentParser(
        description="Decrypt CucumberVPet .cvpkg model packages"
    )
    parser.add_argument("file", type=str, help="Path to .cvpkg file")
    parser.add_argument(
        "-o", "--output", type=str, default=None,
        help="Output directory (default: same dir as .cvpkg)"
    )
    parser.add_argument(
        "-d", "--dry-run", action="store_true",
        help="Decrypt only, don't extract (verify ZIP validity)"
    )
    args = parser.parse_args()

    filepath = Path(args.file)
    if not filepath.exists():
        print(f"Error: File not found: {filepath}")
        sys.exit(1)

    output_dir = args.output or filepath.parent / filepath.stem

    try:
        if args.dry_run:
            zip_data = decrypt_cvpkg(filepath)
            with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                print(f"Valid ZIP, {len(zf.namelist())} files:")
                for name in sorted(zf.namelist())[:20]:
                    print(f"  {name}")
                if len(zf.namelist()) > 20:
                    print(f"  ... and {len(zf.namelist()) - 20} more")
        else:
            result = extract_cvpkg(filepath, output_dir)
            print(f"\nDone: {result}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
