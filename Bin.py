#!/usr/bin/env python3
import argparse
import base64
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import binascii
import json
import sys
from pathlib import Path


def hex_to_bytes(hex_str: str) -> bytes:
    hex_str = hex_str.strip()
    if len(hex_str) % 2 != 0:
        raise ValueError("Hex string must have even length")
    return binascii.unhexlify(hex_str)


def decrypt_aes_cbc_pkcs7(cipher_bytes: bytes, key: bytes, iv: bytes) -> str:
    cipher = AES.new(key, AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(cipher_bytes)
    try:
        plaintext = unpad(decrypted, AES.block_size, style="pkcs7")
    except ValueError as e:
        raise ValueError("Invalid encryption padding; check the file and encryption key") from e
    return plaintext.decode("utf-8")


def count_household_ids(data):
    count = 0
    if isinstance(data, dict):
        for key, value in data.items():
            if key == "householdId":
                count += 1
            count += count_household_ids(value)
    elif isinstance(data, list):
        for item in data:
            count += count_household_ids(item)
    return count


def is_fmr(value):
    """Check decoded FMR signature; accept omitted Base64 padding only."""
    if not isinstance(value, str) or not value:
        return False
    try:
        decoded = base64.b64decode(value + '=' * (-len(value) % 4), validate=True)
        return decoded.startswith(b'FMR')
    except (ValueError, TypeError):
        return False


def check_fingerprint_templates(biometric_data):
    _, present = check_fingerprints(biometric_data)
    return {field: 'FMR signature' if is_fmr(biometric_data[field]) else 'Invalid / non-FMR' for field in present}


def check_fingerprints(biometric_data):
    fingerprint_fields = [
        "leftIndex", "leftMiddle", "leftRing", "leftSmall", "leftThumb",
        "rightIndex", "rightMiddle", "rightRing", "rightSmall", "rightThumb"
    ]
    present_fingerprints = []
    for field in fingerprint_fields:
        if field in biometric_data and biometric_data[field]:
            present_fingerprints.append(field)
    count = len(present_fingerprints)
    return f"{count:02d}", present_fingerprints


def process_person(person_data, prefix="BIN", alt_number=None):
    household_number = person_data.get("houseHoldNumber") or person_data.get("householdNumber") or "N/A"
    first_name = person_data.get("firstName") or person_data.get("firstname") or "Unknown"
    last_name = person_data.get("lastName") or person_data.get("lastname") or ""
    full_name = f"{first_name} {last_name}".strip()

    biometric = person_data.get("biometric") or {}
    photo_status = "01" if biometric.get("photo") else "null"
    fingerprint_count, _ = check_fingerprints(biometric)

    if alt_number:
        output = (
            f"{prefix}-{alt_number}-houseHoldNumber-{household_number}"
            f"__Name: {full_name} __Photo : {photo_status}"
            f"__Fingerprints: {fingerprint_count}"
        )
    else:
        output = (
            f"{prefix}-houseHoldNumber-{household_number}"
            f"__Name: {full_name} __Photo : {photo_status}"
            f"__Fingerprints: {fingerprint_count}"
        )
    print(output)


def print_count_verification(records, household_id_count):
    household_count = len(records)
    bin_fingerprint_count = 0
    alt_counts = [0, 0]
    alt_fingerprint_counts = [0, 0]

    for household in records:
        _, fingerprints = check_fingerprints(household.get("biometric") or {})
        bin_fingerprint_count += bool(fingerprints)

        alternates = household.get("payrollAlternates") or household.get("alternates") or []
        for alt_index, alternate in enumerate(alternates[:2]):
            alt_counts[alt_index] += 1
            _, fingerprints = check_fingerprints(alternate.get("biometric") or {})
            alt_fingerprint_counts[alt_index] += bool(fingerprints)

    comparisons = [
        ("HOUSEHOLD INFORMATION", household_count, "BIN with fingerprint", bin_fingerprint_count),
        # ("ALT-1 + ALT-2", sum(alt_counts), "ALT fingerprint total", sum(alt_fingerprint_counts)),
        ("ALT-1", alt_counts[0], "ALT-1 with fingerprint", alt_fingerprint_counts[0]),
        ("ALT-2", alt_counts[1], "ALT-2 with fingerprint", alt_fingerprint_counts[1]),
    ]

    def print_value(label, value):
        print(f"{label:<28}: {value}")

    def status(record_count, fingerprint_count):
        return "✅ MATCH" if record_count == fingerprint_count else "❌ NOT MATCH"

    print("\n" + "=" * 80)
    print("COUNT VERIFICATION SUMMARY".center(80))
    print("=" * 80 + "\n")
    print_value("Total payment cycle count", household_id_count)
    print("\n" + "-" * 80)
    print_value("Beneficiary Count", household_count)
    print("\n")
    print_value("BIN with fingerprint", bin_fingerprint_count)
    print_value("BIN missing fingerprint", household_count - bin_fingerprint_count)
    print_value("BIN Status", status(household_count, bin_fingerprint_count))
    print()
    print("-" * 80)
    # print_value("ALT-1 + ALT-2 Total", sum(alt_counts))
    # print_value("ALT fingerprint total", sum(alt_fingerprint_counts))
    # print_value("ALT Total Status", status(sum(alt_counts), sum(alt_fingerprint_counts)))
    # print()
    # print("-" * 80)
    for alt_index in range(2):
        label = f"ALT-{alt_index + 1}"
        print_value(f"{label} Total", alt_counts[alt_index])
        print_value(f"{label} with fingerprint", alt_fingerprint_counts[alt_index])
        print_value(f"{label} Status", status(alt_counts[alt_index], alt_fingerprint_counts[alt_index]))
        print()
        print("-" * 80)

    # all_match = all(record_count == fingerprint_count
    #                 for _, record_count, _, fingerprint_count in comparisons)
    # print("-" * 80)
    # print("FINAL RESULT")
    # print("-" * 80)
    # print("✅ ALL COUNTS MATCH" if all_match else "❌ SOME COUNTS DO NOT MATCH")
    # print("=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description="Decrypt SNSOP encrypted payroll .bin file into JSON"
    )
    parser.add_argument("input_bin", help="Path to the encrypted .bin file")
    parser.add_argument(
        "-f", "--folder",
        help="Folder to store decrypted JSON files (default: decrypted_json)",
        default="decrypted_json",
    )
    args = parser.parse_args()
    input_path = Path(args.input_bin)

    if not input_path.exists():
        print(f"❌ File not found: {input_path}")
        sys.exit(1)

    output_folder = Path(args.folder)
    if not output_folder.exists():
        print(f"📁 Folder '{output_folder}' not found. Creating...")
        output_folder.mkdir(parents=True, exist_ok=True)
    else:
        print(f"📁 Folder '{output_folder}' already exists. Using existing folder.")

    hex_key = "dd8de506bd4a90418afe9372f01b979b6f0df2e8aae22ec90b09a0bc197dc80c"
    hex_iv = "4a6f8e2f8a0c5d3e4b6c8d9e0f1a2b3c"
    key = hex_to_bytes(hex_key)
    iv = hex_to_bytes(hex_iv)

    with open(input_path, "rb") as f:
        cipher_bytes = f.read()

    try:
        decrypted_json_str = decrypt_aes_cbc_pkcs7(cipher_bytes, key, iv)
    except Exception as e:
        print("❌ Decryption failed:", e)
        sys.exit(1)

    try:
        json_obj = json.loads(decrypted_json_str)
    except json.JSONDecodeError as e:
        print("❌ Output is not valid JSON:", e)
        sys.exit(1)

    household_count = count_household_ids(json_obj)
    print(f"🏠 Total householdId count: {household_count}")
    print("\n" + "=" * 80 + "\n")

    output_file = output_folder / (input_path.stem + ".json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(json_obj, f, indent=2, ensure_ascii=False)

    print("✅ Decryption successful.")
    print(f"📄 JSON saved to: {output_file}")
    print("\n" + "=" * 80 + "\n")

    # Extract records from payrollHouseHolds (contains person + biometric data)
    if isinstance(json_obj, list):
        records = json_obj
    elif isinstance(json_obj, dict):
        records = (
            json_obj.get("payrollHouseHolds")
            or json_obj.get("payrollDtos")
            or json_obj.get("data")
            or json_obj.get("households")
            or json_obj.get("records")
            or []
        )
    else:
        records = []

    if not records:
        print("⚠️  No records found in JSON.")
        sys.exit(1)

    print(f"📊 HOUSEHOLD INFORMATION ({len(records)} record(s) found):\n")

    for idx, household in enumerate(records):
        process_person(household, "BIN")

        alternates = household.get("payrollAlternates") or household.get("alternates") or []
        for alt_idx, alt_person in enumerate(alternates, 1):
            process_person(alt_person, "ALT", alt_idx)

        if idx < len(records) - 1:
            print()

    print_count_verification(records, household_count)


if __name__ == "__main__":
    main()
