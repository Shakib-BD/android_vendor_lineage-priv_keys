#!/usr/bin/env -S PYTHONDONTWRITEBYTECODE=1 python3
# Copyright (C) 2025 Giovanni Ricca
# SPDX-License-Identifier: Apache-2.0

import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from OpenSSL import crypto

from gen_keys_py import keys
from gen_keys_py.avbtool import AvbTool

# ENV
CERTS_PATH = Path('~/.android-certs').expanduser()
# CERTS_PATH = Path('.android-certs')  # for testing only
RSA_PLATFORM_KEY_SIZE = 4096  # 2048
RSA_APEX_KEY_SIZE = 4096


def extract_public_key(key_apex_path: str, pubkey_output_path: str):
    class Args:
        def __init__(self, key_path, output_path):
            self.key = key_path
            self.output = open(output_path, 'wb')

    args = Args(key_apex_path, pubkey_output_path)

    tool = AvbTool()
    tool.extract_public_key(args)


def subject_params(param: str, custom_param: dict = None):
    # Adapt this list based on https://learn.microsoft.com/en-us/previous-versions/windows/desktop/ldap/distinguished-names.
    defaults = {
        'C': {1: 'US'},
        'ST': {1: 'California'},
        'L': {1: 'Mountain View'},
        'O': {1: 'Android'},
        'OU': {1: 'Android'},
        'CN': {1: 'Android'},
        'emailAddress': {1: 'android@android.com'},
    }

    if custom_param and param in custom_param:
        defaults[param] = {1: custom_param[param]}

    return defaults.get(param)


def generate_single_platform_key(cert: str):
    key_platform = CERTS_PATH / f'{cert}.pem'
    x509_file = Path(f'{cert}.x509.pem')
    pk8_file = Path(f'{cert}.pk8')

    if any(not path.exists() for path in [key_platform, x509_file, pk8_file]):
        # Generate key_platform
        key = crypto.PKey()
        key.generate_key(crypto.TYPE_RSA, RSA_PLATFORM_KEY_SIZE)
        key_platform.write_bytes(
            crypto.dump_privatekey(crypto.FILETYPE_PEM, key)
        )

        # Generate x509_file
        cert_obj = crypto.X509()
        cert_obj.get_subject().C = subject_params('C')[1]
        cert_obj.get_subject().ST = subject_params('ST')[1]
        cert_obj.get_subject().L = subject_params('L')[1]
        cert_obj.get_subject().O = subject_params('O')[1]
        cert_obj.get_subject().OU = subject_params('OU')[1]
        cert_obj.get_subject().CN = subject_params('CN')[1]
        cert_obj.get_subject().emailAddress = subject_params('emailAddress')[1]

        cert_obj.set_serial_number(1)
        cert_obj.gmtime_adj_notBefore(0)
        cert_obj.gmtime_adj_notAfter(10000 * 24 * 60 * 60)  # 10000 days
        cert_obj.set_issuer(cert_obj.get_subject())
        cert_obj.set_pubkey(key)
        cert_obj.sign(key, 'sha256')

        x509_file.write_bytes(
            crypto.dump_certificate(crypto.FILETYPE_PEM, cert_obj)
        )

        # Generate pk8_file
        pkey = serialization.load_pem_private_key(
            crypto.dump_privatekey(crypto.FILETYPE_PEM, key), password=None
        )

        pk8_der = pkey.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        pk8_file.write_bytes(pk8_der)

    return str(key_platform), str(x509_file), str(pk8_file)


def generate_single_apex_key(apex: str):
    key_apex = Path(f'{apex}.pem')
    x509_file = Path(f'{apex}.certificate.override.x509.pem')
    pk8_file = Path(f'{apex}.certificate.override.pk8')
    avbpubkey_file = Path(f'{apex}.avbpubkey')
    pubkey_file = Path(f'{apex}.pubkey')

    if any(not path.exists() for path in [key_apex, x509_file, pk8_file]) or (
        not pubkey_file.exists() and not avbpubkey_file.exists()
    ):
        # Generate key_apex
        key = crypto.PKey()
        key.generate_key(crypto.TYPE_RSA, RSA_APEX_KEY_SIZE)
        key_apex.write_bytes(crypto.dump_privatekey(crypto.FILETYPE_PEM, key))

        # Generate avbpubkey_file / pubkey_file
        if apex == 'com.android.vndk':
            extract_public_key(key_apex, pubkey_file)
        else:
            extract_public_key(key_apex, avbpubkey_file)

        # Generate x509_file
        cert_obj = crypto.X509()
        cert_obj.get_subject().C = subject_params('C')[1]
        cert_obj.get_subject().ST = subject_params('ST')[1]
        cert_obj.get_subject().L = subject_params('L')[1]
        cert_obj.get_subject().O = subject_params('O')[1]
        cert_obj.get_subject().OU = subject_params('OU')[1]
        cert_obj.get_subject().CN = subject_params('CN', {'CN': apex})[1]
        cert_obj.get_subject().emailAddress = subject_params('emailAddress')[1]

        cert_obj.set_serial_number(1)
        cert_obj.gmtime_adj_notBefore(0)
        cert_obj.gmtime_adj_notAfter(10000 * 24 * 60 * 60)  # 10000 days
        cert_obj.set_issuer(cert_obj.get_subject())
        cert_obj.set_pubkey(key)
        cert_obj.sign(key, 'sha256')

        x509_file.write_bytes(
            crypto.dump_certificate(crypto.FILETYPE_PEM, cert_obj)
        )

        # Generate pk8_file
        pkey = serialization.load_pem_private_key(
            crypto.dump_privatekey(crypto.FILETYPE_PEM, key), password=None
        )

        pk8_der = pkey.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        pk8_file.write_bytes(pk8_der)

    return (
        str(key_apex),
        str(x509_file),
        str(pk8_file),
        str(avbpubkey_file),
        str(pubkey_file),
    )


def generate_keys():
    workers = mp.cpu_count()
    CERTS_PATH.mkdir(parents=True, exist_ok=True)

    with ProcessPoolExecutor(max_workers=workers) as executor:
        platform_futures = [
            executor.submit(generate_single_platform_key, cert)
            for cert in keys.platform_keys
        ]
        apex_futures = [
            executor.submit(generate_single_apex_key, apex)
            for apex in keys.apex_keys
        ]

        platform_results = [future.result() for future in platform_futures]
        apex_results = [future.result() for future in apex_futures]

    return platform_results, apex_results


def generate_android_bp() -> None:
    cert_blocks = '\n\n'.join(
        f'android_app_certificate {{\n'
        f'    name: "{apex}.certificate.override",\n'
        f'    certificate: "{apex}.certificate.override",\n'
        f'}}'
        for apex in keys.apex_keys
    )

    content = f'// DO NOT EDIT THIS FILE MANUALLY\n\n{cert_blocks}\n'
    Path('Android.bp').write_text(content)


def generate_makefile():
    mk_file = Path('keys.mk')

    sections = [
        '# DO NOT EDIT THIS FILE MANUALLY',
        '',
        'PRODUCT_CERTIFICATE_OVERRIDES := \\',
        '\n'.join(
            f"    {key}:{key}.certificate.override{' \\' if i < len(keys.apex_keys)-1 else ''}"
            for i, key in enumerate(keys.apex_keys)
        ),
        '',
        'PRODUCT_CERTIFICATE_OVERRIDES += \\',
        '\n'.join(
            f"    {key}:com.android.hardware.certificate.override{' \\' if i < len(keys.apex_hardware_keys)-1 else ''}"
            for i, key in enumerate(keys.apex_hardware_keys)
        ),
        '',
        'PRODUCT_CERTIFICATE_OVERRIDES += \\',
        '\n'.join(
            f"    {key}{' \\' if i < len(keys.apex_app_keys)-1 else ''}"
            for i, key in enumerate(keys.apex_app_keys)
        ),
        '',
        'PRODUCT_DEFAULT_DEV_CERTIFICATE := vendor/lineage-priv/keys/releasekey',
        'PRODUCT_EXTRA_RECOVERY_KEYS += vendor/lineage-priv/keys/signed',
        '',
    ]

    mk_file.write_text('\n'.join(sections))


def main():
    generate_keys()
    generate_android_bp()
    generate_makefile()


main()
