# crypto_utils.py
"""AES-256-GCM 加密/解密工具"""
import os
import json
from hashlib import sha256

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad


def derive_key(password: str, salt: bytes = None) -> (bytes, bytes):
    """使用 PBKDF2 派生 256 位密钥"""
    from Crypto.Protocol.KDF import PBKDF2
    if salt is None:
        salt = os.urandom(16)
    key = PBKDF2(password, salt, dkLen=32, count=100000)
    return key, salt


def encrypt_config(plain_data: dict, master_key: bytes) -> bytes:
    """加密配置字典，返回密文字节（含 nonce 和 tag）"""
    plaintext = json.dumps(plain_data, ensure_ascii=False).encode('utf-8')
    nonce = os.urandom(12)
    cipher = AES.new(master_key, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    # 封装：nonce + tag + ciphertext
    return nonce + tag + ciphertext


def decrypt_config(cipherdata: bytes, master_key: bytes) -> dict:
    """解密配置，返回配置字典"""
    nonce = cipherdata[:12]
    tag = cipherdata[12:28]
    ciphertext = cipherdata[28:]
    cipher = AES.new(master_key, AES.MODE_GCM, nonce=nonce)
    plaintext = cipher.decrypt_and_verify(ciphertext, tag)
    return json.loads(plaintext.decode('utf-8'))