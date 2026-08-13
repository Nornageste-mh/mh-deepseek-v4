# key_manager.py
"""
密钥管理器 - 基于 Shamir 秘密共享 + AES-256-GCM
分片存储于 ~/.config/mh-agent/keys/
"""
import os
import json
import logging
import time
from pathlib import Path

from Crypto.Protocol.SecretSharing import Shamir
from Crypto.Random import get_random_bytes

from config import SHARD_N, SHARD_K, SHARD_DIR, ENCRYPTED_CONFIG_FILE, KEY_LOCK_TIMEOUT
from crypto_utils import encrypt_config, decrypt_config

logger = logging.getLogger("MHAgent.KeyManager")


class KeyManager:
    def __init__(self):
        self.shard_dir = Path(SHARD_DIR)
        self.shard_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = Path(ENCRYPTED_CONFIG_FILE)
        self._master_key: bytes | None = None
        self._decrypted_config: dict | None = None
        self._last_access: float = 0.0
        self._locked: bool = True

    def is_initialized(self) -> bool:
        """检查是否已经生成分片"""
        shards = list(self.shard_dir.glob("shard_*.key"))
        return len(shards) >= SHARD_K

    def initialize(self) -> list[tuple[int, bytes]]:
        """
        首次初始化：生成主密钥，分割为分片，返回分片列表（供用户保管）。
        """
        master_key = get_random_bytes(16)  # 128-bit (Shamir 要求 16 字节)
        # 分割为 SHARD_N 个分片，需要 SHARD_K 个恢复
        shares = Shamir.split(SHARD_K, SHARD_N, master_key)
        # 本地存储所有分片（序列化为 JSON）
        for i, share in enumerate(shares):
            share_path = self.shard_dir / f"shard_{i}.key"
            share_data = json.dumps({"index": share[0], "share": share[1].hex()})
            share_path.write_text(share_data, encoding='utf-8')
        logger.info(f"已生成 {SHARD_N} 个分片，存储在 {self.shard_dir}")
        self._master_key = master_key
        self._locked = False
        self._last_access = time.time()
        return shares

    def unlock(self, explicit_shards: list[tuple[int, bytes]] = None) -> bool:
        """
        尝试恢复主密钥。
        如果提供 explicit_shards，使用它们恢复；
        否则从默认存储目录读取分片。
        """
        if not self._locked and self._master_key is not None:
            return True
        try:
            if explicit_shards and len(explicit_shards) >= SHARD_K:
                self._master_key = Shamir.combine(explicit_shards[:SHARD_K])
            else:
                share_paths = sorted(self.shard_dir.glob("shard_*.key"))
                if len(share_paths) < SHARD_K:
                    logger.error(f"分片不足：需要 {SHARD_K} 个，找到 {len(share_paths)} 个")
                    return False
                shares = []
                for p in share_paths[:SHARD_K]:
                    data = json.loads(p.read_text(encoding='utf-8'))
                    shares.append((data["index"], bytes.fromhex(data["share"])))
                self._master_key = Shamir.combine(shares)
            self._locked = False
            self._last_access = time.time()
            logger.info("主密钥已恢复")
            return True
        except Exception as e:
            logger.error(f"密钥恢复失败: {e}")
            return False

    def lock(self):
        """锁定内存中的密钥（30分钟无操作自动调用）"""
        self._master_key = None
        self._decrypted_config = None
        self._locked = True
        logger.info("密钥已锁定（从内存中清除）")

    def check_timeout(self):
        """检查是否超时，若超时自动锁定"""
        if not self._locked and self._master_key is not None:
            if time.time() - self._last_access > KEY_LOCK_TIMEOUT:
                self.lock()

    def _ensure_unlocked(self):
        if self._locked or self._master_key is None:
            if self.is_initialized():
                if self.unlock():
                    return
            raise RuntimeError("密钥管理器已锁定，请先调用 unlock()")

    def get_api_keys(self) -> dict:
        """获取解密后的 API Key 字典（自动解锁）"""
        if self._locked or self._master_key is None:
            self.unlock()
        if self._decrypted_config is not None:
            self._last_access = time.time()
            return self._decrypted_config
        if not self.config_file.exists():
            logger.warning("加密配置文件不存在，返回空字典")
            return {}
        if self._master_key is None:
            logger.error("主密钥不可用（分片损坏或解锁失败），无法解密配置")
            return {}
        cipherdata = self.config_file.read_bytes()
        self._decrypted_config = decrypt_config(cipherdata, self._master_key)
        self._last_access = time.time()
        return self._decrypted_config

    def save_api_keys(self, keys_dict: dict):
        """加密并保存 API Key 字典（自动解锁）"""
        if self._locked or self._master_key is None:
            self.unlock()
        cipherdata = encrypt_config(keys_dict, self._master_key)
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.config_file.write_bytes(cipherdata)
        self._decrypted_config = keys_dict
        self._last_access = time.time()
        logger.info(f"API Keys 已加密保存到 {self.config_file}")

    def update_api_key(self, name: str, value: str):
        """更新单个 API Key"""
        keys = self.get_api_keys()
        keys[name] = value
        self.save_api_keys(keys)