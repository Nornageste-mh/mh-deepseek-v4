"""
网络安全工具模块（跨平台：Windows/Linux/Android/macOS）
提供全面的网络安全检测与分析能力。

依赖（均为跨平台）：
- 标准库: ssl, socket, hashlib, hmac, ipaddress, base64, struct
- 可选: cryptography, dnspython, requests
"""
import sys
import os
import ssl
import socket
import hashlib
import hmac
import ipaddress
import base64
import struct
import logging
import json
import re
import time
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("MHAgent.Tools.NetworkSecurity")

IS_WINDOWS = sys.platform == "win32"

# ═══════════════════════════════════════════════════════════════
# 工具注册
# ═══════════════════════════════════════════════════════════════

def register_tools(registry):
    # ── 网络扫描与探测 ──
    registry.register("dns_lookup", _dns_lookup,
                      "DNS解析查询：获取域名对应的A/AAAA/MX/TXT/NS/CNAME记录",
                      {"domain": {"type": "string", "description": "要查询的域名"},
                       "record_type": {"type": "string", "description": "记录类型: A/AAAA/MX/TXT/NS/CNAME/ANY，默认A"},
                       "dns_server": {"type": "string", "description": "指定DNS服务器(可选)"}})
    
    registry.register("reverse_dns", _reverse_dns,
                      "反向DNS查询：根据IP地址查询对应的域名",
                      {"ip": {"type": "string", "description": "IP地址"}})
    
    registry.register("port_scan", _port_scan,
                      "TCP端口扫描：扫描指定主机的端口开放情况（支持范围扫描）",
                      {"host": {"type": "string", "description": "目标主机IP或域名"},
                       "ports": {"type": "string", "description": "端口范围，如 '22,80,443' 或 '1-1000'，默认'21,22,23,25,53,80,110,143,443,445,993,995,1433,1521,2049,3306,3389,5432,6379,8080,8443,27017'"},
                       "timeout": {"type": "integer", "description": "超时时间(秒)，默认2"}})
    
    registry.register("ping", _ping,
                      "ICMP Ping检测：测试主机连通性（跨平台）",
                      {"host": {"type": "string", "description": "目标主机IP或域名"},
                       "count": {"type": "integer", "description": "发送次数，默认4"}})
    
    registry.register("traceroute", _traceroute,
                      "路由追踪：显示数据包到达目标主机的路径（跨平台）",
                      {"host": {"type": "string", "description": "目标主机IP或域名"},
                       "max_hops": {"type": "integer", "description": "最大跳数，默认30"}})
    
    # ── SSL/TLS 安全检测 ──
    registry.register("ssl_cert_info", _ssl_cert_info,
                      "获取SSL/TLS证书信息：检查证书有效期、颁发者、主题、SAN等",
                      {"host": {"type": "string", "description": "目标主机"},
                       "port": {"type": "integer", "description": "端口，默认443"}})
    
    registry.register("ssl_cert_chain", _ssl_cert_chain,
                      "获取SSL/TLS完整证书链：包含各级CA证书信息",
                      {"host": {"type": "string", "description": "目标主机"},
                       "port": {"type": "integer", "description": "端口，默认443"}})
    
    registry.register("ssl_cipher_check", _ssl_cipher_check,
                      "检查SSL/TLS支持的密码套件：检测是否支持弱加密算法",
                      {"host": {"type": "string", "description": "目标主机"},
                       "port": {"type": "integer", "description": "端口，默认443"}})
    
    registry.register("ssl_protocol_check", _ssl_protocol_check,
                      "检查SSL/TLS协议版本支持情况：检测是否支持SSLv2/SSLv3/TLSv1.0等不安全协议",
                      {"host": {"type": "string", "description": "目标主机"},
                       "port": {"type": "integer", "description": "端口，默认443"}})
    
    # ── 安全检测与分析 ──
    registry.register("security_headers_check", _security_headers_check,
                      "检查HTTP安全响应头：检测HSTS、CSP、X-Frame-Options等安全头配置",
                      {"url": {"type": "string", "description": "目标URL"}})
    
    registry.register("hash_file", _hash_file,
                      "计算文件哈希值：支持MD5/SHA1/SHA256/SHA512/SHA3-256/BLAKE2b",
                      {"file_path": {"type": "string", "description": "文件路径"},
                       "algorithm": {"type": "string", "description": "哈希算法: md5/sha1/sha256/sha512/sha3_256/blake2b，默认sha256"}})
    
    registry.register("hash_text", _hash_text,
                      "计算文本哈希值：支持MD5/SHA1/SHA256/SHA512/HMAC",
                      {"text": {"type": "string", "description": "要哈希的文本"},
                       "algorithm": {"type": "string", "description": "哈希算法: md5/sha1/sha256/sha512，默认sha256"},
                       "key": {"type": "string", "description": "HMAC密钥(仅HMAC算法需要)"}})
    
    registry.register("ip_geolocation", _ip_geolocation,
                      "IP地址地理位置查询：获取IP所属国家、城市、ISP等信息",
                      {"ip": {"type": "string", "description": "IP地址"}})
    
    registry.register("subnet_calculator", _subnet_calculator,
                      "子网计算器：计算网络地址、广播地址、可用主机范围等",
                      {"network": {"type": "string", "description": "CIDR表示法，如 '192.168.1.0/24'"}})
    
    registry.register("mac_address_lookup", _mac_address_lookup,
                      "MAC地址厂商查询：根据MAC地址查询设备制造商",
                      {"mac": {"type": "string", "description": "MAC地址，如 '00:1A:2B:3C:4D:5E'"}})
    
    # ── 网络安全扫描 ──
    registry.register("check_vulnerable_ports", _check_vulnerable_ports,
                      "检查常见脆弱端口：检测是否暴露了高风险服务端口",
                      {"host": {"type": "string", "description": "目标主机IP或域名"}})
    
    registry.register("http_security_scan", _http_security_scan,
                      "HTTP安全扫描：检查HTTPS强制跳转、HTTP方法、CORS配置等",
                      {"url": {"type": "string", "description": "目标URL"}})
    
    registry.register("check_open_relay", _check_open_relay,
                      "检查SMTP开放中继：检测邮件服务器是否配置为开放中继",
                      {"host": {"type": "string", "description": "邮件服务器地址"},
                       "port": {"type": "integer", "description": "端口，默认25"}})
    
    # ── 编码/解码工具 ──
    registry.register("base64_encode", _base64_encode,
                      "Base64编码",
                      {"text": {"type": "string", "description": "要编码的文本"}})
    
    registry.register("base64_decode", _base64_decode,
                      "Base64解码",
                      {"encoded": {"type": "string", "description": "要解码的Base64字符串"}})
    
    registry.register("hex_encode", _hex_encode,
                      "十六进制编码",
                      {"text": {"type": "string", "description": "要编码的文本"}})
    
    registry.register("hex_decode", _hex_decode,
                      "十六进制解码",
                      {"encoded": {"type": "string", "description": "要解码的十六进制字符串"}})
    
    # ── 网络安全信息查询 ──
    registry.register("whois_lookup", _whois_lookup,
                      "WHOIS域名信息查询：获取域名注册信息（需安装python-whois）",
                      {"domain": {"type": "string", "description": "要查询的域名"}})
    
    registry.register("certificate_transparency", _certificate_transparency,
                      "证书透明度日志查询：查询域名在CT日志中的SSL证书记录",
                      {"domain": {"type": "string", "description": "要查询的域名"}})
    
    logger.info("✅ 网络安全工具模块已注册 (20+ 工具)")


# ═══════════════════════════════════════════════════════════════
# DNS 工具
# ═══════════════════════════════════════════════════════════════

def _dns_lookup(domain: str, record_type: str = "A", dns_server: str = None, **kwargs) -> str:
    """DNS 解析查询"""
    record_type = record_type.upper()
    
    # 优先使用 dnspython（如已安装）
    try:
        import dns.resolver
        import dns.rdatatype
        
        resolver = dns.resolver.Resolver()
        if dns_server:
            resolver.nameservers = [dns_server]
        resolver.timeout = 5
        resolver.lifetime = 10
        
        results = []
        try:
            answers = resolver.resolve(domain, record_type)
            for rdata in answers:
                results.append(str(rdata))
        except dns.resolver.NoAnswer:
            return f"DNS查询完成：{domain} 的 {record_type} 记录无结果"
        except dns.resolver.NXDOMAIN:
            return f"域名不存在：{domain}"
        except dns.exception.Timeout:
            return f"DNS查询超时：{domain}"
        except dns.resolver.NoNameservers:
            return f"无可用DNS服务器"
        
        if not results:
            return f"DNS查询完成：{domain} 的 {record_type} 记录为空"
        
        return (f"✅ DNS {record_type} 记录查询: {domain}\n"
                f"服务器: {dns_server or '系统默认'}\n"
                f"结果 ({len(results)} 条):\n" + "\n".join(f"  {r}" for r in results))
    
    except ImportError:
        pass
    
    # 降级：使用 socket / subprocess
    try:
        if record_type == "A":
            ip = socket.getaddrinfo(domain, None, socket.AF_INET)
            results = list(set(addr[4][0] for addr in ip))
            if results:
                return (f"✅ DNS A 记录查询: {domain}\n"
                        f"结果 ({len(results)} 条):\n" + "\n".join(f"  {r}" for r in results))
            return f"DNS查询完成：{domain} 无A记录"
        
        elif record_type == "AAAA":
            ip = socket.getaddrinfo(domain, None, socket.AF_INET6)
            results = list(set(addr[4][0] for addr in ip))
            if results:
                return (f"✅ DNS AAAA 记录查询: {domain}\n"
                        f"结果 ({len(results)} 条):\n" + "\n".join(f"  {r}" for r in results))
            return f"DNS查询完成：{domain} 无AAAA记录"
        
        elif record_type == "MX":
            # 使用 nslookup 或 host 命令
            if IS_WINDOWS:
                cmd = f"nslookup -type=MX {domain} 2>nul"
            else:
                cmd = f"nslookup -type=MX {domain} 2>/dev/null || host -t MX {domain} 2>/dev/null"
            
            if dns_server:
                cmd += f" {dns_server}"
            
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
            output = r.stdout or r.stderr
            if "mail exchanger" in output.lower() or "mx" in output.lower():
                lines = [l.strip() for l in output.split('\n') if 'mail exchanger' in l.lower() or 'MX' in l]
                return f"✅ DNS MX 记录查询: {domain}\n" + "\n".join(lines[:10])
            return f"DNS MX 查询完成，但未解析到结果。\n原始输出:\n{output[:500]}"
        
        else:
            # 通用降级
            if IS_WINDOWS:
                cmd = f"nslookup -type={record_type} {domain} 2>nul"
            else:
                cmd = f"nslookup -type={record_type} {domain} 2>/dev/null || dig {domain} {record_type} 2>/dev/null"
            
            if dns_server:
                cmd += f" {dns_server}"
            
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
            output = (r.stdout or r.stderr)[:1000]
            if output.strip():
                return f"✅ DNS {record_type} 记录查询: {domain}\n{output}"
            return f"DNS查询完成：{domain} 的 {record_type} 记录无结果"
    
    except socket.gaierror as e:
        return f"DNS解析失败: {e}"
    except Exception as e:
        return f"DNS查询异常: {e}"


def _reverse_dns(ip: str, **kwargs) -> str:
    """反向DNS查询"""
    try:
        hostname, aliases, addresses = socket.gethostbyaddr(ip)
        result = f"✅ 反向DNS查询: {ip}\n"
        result += f"  主机名: {hostname}\n"
        if aliases:
            result += f"  别名: {', '.join(aliases)}\n"
        return result
    except socket.herror:
        return f"反向DNS查询完成：{ip} 无PTR记录"
    except Exception as e:
        return f"反向DNS查询失败: {e}"


# ═══════════════════════════════════════════════════════════════
# 端口扫描
# ═══════════════════════════════════════════════════════════════

# 常见服务端口映射
COMMON_PORTS = {
    21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS",
    80: "HTTP", 110: "POP3", 143: "IMAP", 443: "HTTPS", 445: "SMB",
    993: "IMAPS", 995: "POP3S", 1433: "MSSQL", 1521: "Oracle",
    2049: "NFS", 3306: "MySQL", 3389: "RDP", 5432: "PostgreSQL",
    6379: "Redis", 8080: "HTTP-Proxy", 8443: "HTTPS-Alt",
    27017: "MongoDB"
}

# 高危端口（通常不应暴露在公网）
HIGH_RISK_PORTS = {
    21: "FTP-明文传输", 23: "Telnet-明文", 135: "RPC", 139: "NetBIOS",
    445: "SMB-勒索软件高危", 1433: "MSSQL-数据库", 1521: "Oracle-数据库",
    2049: "NFS-无认证", 3306: "MySQL-数据库", 3389: "RDP-远程桌面",
    5432: "PostgreSQL-数据库", 6379: "Redis-未授权", 27017: "MongoDB-数据库",
    9200: "Elasticsearch", 11211: "Memcached-DDoS放大"
}


def _port_scan(host: str, ports: str = None, timeout: int = 2, **kwargs) -> str:
    """TCP端口扫描"""
    # 解析端口范围
    if ports:
        port_list = _parse_ports(ports)
    else:
        port_list = list(COMMON_PORTS.keys())
    
    if not port_list:
        return "端口格式错误，请使用如 '22,80,443' 或 '1-1000'"
    
    # 解析主机
    target_ip = _resolve_host(host)
    if not target_ip:
        return f"无法解析主机: {host}"
    
    # 扫描
    open_ports = []
    closed_count = 0
    timeout = max(1, min(timeout, 10))
    
    start_time = time.time()
    
    for port in port_list:
        result = _check_tcp_port(target_ip, port, timeout)
        if result == "open":
            service = COMMON_PORTS.get(port, "Unknown")
            risk = "⚠️ 高危" if port in HIGH_RISK_PORTS else ""
            open_ports.append((port, service, risk))
        elif result == "closed":
            closed_count += 1
        # filtered 不计数
    
    duration = time.time() - start_time
    
    # 格式化结果
    lines = [f"✅ 端口扫描完成: {host} ({target_ip})"]
    lines.append(f"  扫描端口: {len(port_list)} 个 | 开放: {len(open_ports)} | 关闭/过滤: {closed_count}")
    lines.append(f"  扫描耗时: {duration:.1f} 秒")
    
    if open_ports:
        lines.append(f"\n  📌 开放端口:")
        for port, service, risk in sorted(open_ports, key=lambda x: x[0]):
            lines.append(f"    {port:>5}/tcp  {service:<15} {risk}")
    else:
        lines.append(f"\n  📌 未发现开放端口")
    
    # 安全建议
    high_risk_found = [p for p, _, r in open_ports if r]
    if high_risk_found:
        lines.append(f"\n  ⚠️ 安全警告:")
        for port in high_risk_found:
            service = COMMON_PORTS.get(port, "Unknown")
            risk_desc = HIGH_RISK_PORTS.get(port, "高风险服务")
            lines.append(f"    端口 {port} ({service}): {risk_desc}")
        lines.append(f"  建议：如非必要，请关闭或限制这些端口的公网访问。")
    
    return "\n".join(lines)


def _check_vulnerable_ports(host: str, **kwargs) -> str:
    """检查常见脆弱端口"""
    return _port_scan(host, ports=",".join(str(p) for p in HIGH_RISK_PORTS.keys()), timeout=3)


def _parse_ports(ports_str: str) -> List[int]:
    """解析端口范围字符串"""
    result = set()
    parts = ports_str.split(',')
    for part in parts:
        part = part.strip()
        if '-' in part:
            try:
                start, end = part.split('-', 1)
                start, end = int(start.strip()), int(end.strip())
                if 1 <= start <= end <= 65535:
                    result.update(range(start, end + 1))
            except ValueError:
                continue
        else:
            try:
                port = int(part)
                if 1 <= port <= 65535:
                    result.add(port)
            except ValueError:
                continue
    return sorted(result)


def _resolve_host(host: str) -> Optional[str]:
    """解析主机名到IP地址"""
    # 检查是否是IP
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    
    # DNS解析
    try:
        return socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]
    except Exception:
        return None


def _check_tcp_port(ip: str, port: int, timeout: int = 2) -> str:
    """检测单个TCP端口状态"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, port))
        sock.close()
        if result == 0:
            return "open"
        elif result == 111:  # Connection refused
            return "closed"
        else:
            return "filtered"
    except socket.timeout:
        return "filtered"
    except Exception:
        return "filtered"


# ═══════════════════════════════════════════════════════════════
# Ping & Traceroute
# ═══════════════════════════════════════════════════════════════

def _ping(host: str, count: int = 4, **kwargs) -> str:
    """跨平台 Ping 检测"""
    count = max(1, min(count, 100))
    
    try:
        if IS_WINDOWS:
            cmd = f"ping -n {count} {host}"
        else:
            cmd = f"ping -c {count} -W 5 {host}"
        
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        output = r.stdout or r.stderr
        
        # 提取关键信息
        lines = output.split('\n')
        summary_lines = []
        
        if IS_WINDOWS:
            for line in lines:
                if any(kw in line.lower() for kw in ['回复', '来自', '请求超时', '统计', '最短', '平均', '丢失', 'packets', 'approximate', 'minimum', 'average', 'maximum']):
                    summary_lines.append(line.strip())
        else:
            for line in lines:
                if any(kw in line.lower() for kw in ['bytes from', 'icmp_seq', 'statistics', 'packet loss', 'min/avg/max', 'rtt']):
                    summary_lines.append(line.strip())
        
        if summary_lines:
            return (f"✅ Ping {host} ({count} 次)\n" + "\n".join(summary_lines[:20]))
        
        # 如果没有提取到关键行，返回原始输出摘要
        output_clean = "\n".join(lines[:30])
        return f"✅ Ping {host} 完成\n{output_clean}"
    
    except subprocess.TimeoutExpired:
        return f"Ping {host} 超时（30秒）"
    except FileNotFoundError:
        return "Ping 命令不可用（当前环境不支持）"
    except Exception as e:
        return f"Ping 失败: {e}"


def _traceroute(host: str, max_hops: int = 30, **kwargs) -> str:
    """跨平台路由追踪"""
    max_hops = max(5, min(max_hops, 64))
    
    try:
        if IS_WINDOWS:
            cmd = f"tracert -h {max_hops} {host}"
        else:
            cmd = f"traceroute -m {max_hops} -w 3 {host}"
        
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
        output = r.stdout or r.stderr
        
        lines = output.split('\n')
        # 过滤掉无关行，保留路由信息
        relevant = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith('Tracing') and 'traceroute' not in stripped.lower():
                if re.search(r'\d+\s', stripped) or 'ms' in stripped or '*' in stripped:
                    relevant.append(stripped)
        
        if relevant:
            return f"✅ 路由追踪: {host} (最多 {max_hops} 跳)\n" + "\n".join(relevant[:max_hops])
        
        # 回退
        return f"✅ 路由追踪: {host}\n{output[:2000]}"
    
    except subprocess.TimeoutExpired:
        return f"路由追踪 {host} 超时"
    except FileNotFoundError:
        return "路由追踪命令不可用（当前环境不支持）"
    except Exception as e:
        return f"路由追踪失败: {e}"


# ═══════════════════════════════════════════════════════════════
# SSL/TLS 安全检测
# ═══════════════════════════════════════════════════════════════

def _ssl_cert_info(host: str, port: int = 443, **kwargs) -> str:
    """获取SSL证书信息"""
    try:
        context = ssl.create_default_context()
        # 不验证主机名，只获取信息
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        
        with socket.create_connection((host, port), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert(binary_form=False)
                version = ssock.version()
                cipher = ssock.cipher()
        
        if not cert:
            return f"无法获取 {host}:{port} 的SSL证书信息"
        
        # 提取关键信息
        subject = dict(x[0] for x in cert.get('subject', []))
        issuer = dict(x[0] for x in cert.get('issuer', []))
        
        not_before = cert.get('notBefore', 'N/A')
        not_after = cert.get('notAfter', 'N/A')
        
        # 计算剩余天数
        remaining_days = "N/A"
        try:
            from datetime import datetime
            expiry = datetime.strptime(not_after, '%b %d %H:%M:%S %Y %Z')
            remaining_days = (expiry - datetime.now()).days
        except Exception:
            pass
        
        san_list = []
        for ext in cert.get('subjectAltName', []):
            san_list.append(f"{ext[0]}: {ext[1]}")
        
        result = [
            f"✅ SSL证书信息: {host}:{port}",
            f"  TLS版本: {version}",
            f"  密码套件: {cipher[0]} ({cipher[1]}位)",
            f"  主题: CN={subject.get('commonName', 'N/A')}, O={subject.get('organizationName', 'N/A')}",
            f"  颁发者: CN={issuer.get('commonName', 'N/A')}, O={issuer.get('organizationName', 'N/A')}",
            f"  序列号: {cert.get('serialNumber', 'N/A')}",
            f"  生效时间: {not_before}",
            f"  过期时间: {not_after}",
            f"  剩余天数: {remaining_days} 天",
        ]
        
        if remaining_days != "N/A":
            if remaining_days < 0:
                result.append(f"  ⚠️ 证书已过期 {abs(remaining_days)} 天！")
            elif remaining_days < 30:
                result.append(f"  ⚠️ 证书将在 {remaining_days} 天后过期，请及时续期！")
            else:
                result.append(f"  ✅ 证书有效期正常")
        
        if san_list:
            result.append(f"  SAN (主体备用名称):")
            for san in san_list[:10]:
                result.append(f"    {san}")
            if len(san_list) > 10:
                result.append(f"    ... 共 {len(san_list)} 项")
        
        return "\n".join(result)
    
    except socket.timeout:
        return f"连接超时: {host}:{port}"
    except socket.gaierror:
        return f"无法解析主机: {host}"
    except ConnectionRefusedError:
        return f"连接被拒绝: {host}:{port}"
    except ssl.SSLError as e:
        return f"SSL错误: {e}"
    except Exception as e:
        return f"获取证书信息失败: {e}"


def _ssl_cert_chain(host: str, port: int = 443, **kwargs) -> str:
    """获取SSL完整证书链"""
    try:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        
        with socket.create_connection((host, port), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=host) as ssock:
                # 获取证书链
                cert_binary_chain = ssock.getpeercert(binary_form=True)
                # 获取对等证书链
                cert_chain = []
                try:
                    # 尝试获取完整链（Python 3.10+）
                    cert_chain = ssock.get_verified_chain()
                except AttributeError:
                    cert_chain = [ssock.getpeercert(binary_form=True)]
        
        result = [f"✅ SSL证书链: {host}:{port}"]
        result.append(f"  证书链长度: {len(cert_chain)} 级")
        
        for i, cert_bytes in enumerate(cert_chain):
            try:
                from cryptography import x509
                from cryptography.hazmat.backends import default_backend
                cert = x509.load_der_x509_certificate(cert_bytes, default_backend())
                
                subject = cert.subject.rfc4514_string()
                issuer = cert.issuer.rfc4514_string()
                not_before = cert.not_valid_before_utc.strftime("%Y-%m-%d %H:%M:%S")
                not_after = cert.not_valid_after_utc.strftime("%Y-%m-%d %H:%M:%S")
                
                result.append(f"\n  [{i+1}] 证书层级 {i+1}")
                result.append(f"      主题: {subject}")
                result.append(f"      颁发者: {issuer}")
                result.append(f"      有效期: {not_before} ~ {not_after}")
                result.append(f"      序列号: {cert.serial_number}")
                
            except ImportError:
                result.append(f"\n  [{i+1}] 证书 (安装 cryptography 可查看详情)")
            except Exception:
                result.append(f"\n  [{i+1}] 证书 (解析失败)")
        
        return "\n".join(result)
    
    except ImportError:
        return _ssl_cert_info(host, port) + "\n\n(安装 cryptography 库可查看完整证书链: pip install cryptography)"
    except Exception as e:
        return f"获取证书链失败: {e}"


def _ssl_cipher_check(host: str, port: int = 443, **kwargs) -> str:
    """检查SSL/TLS密码套件安全性"""
    # 弱密码套件列表
    WEAK_CIPHERS = [
        'RC4', 'DES', '3DES', 'MD5', 'EXPORT', 'NULL', 'anon',
        'IDEA', 'SEED', 'CAMELLIA', 'PSK', 'SRP'
    ]
    
    try:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        
        with socket.create_connection((host, port), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=host) as ssock:
                current_cipher = ssock.cipher()
                version = ssock.version()
        
        result = [
            f"✅ SSL/TLS 密码套件检查: {host}:{port}",
            f"  当前TLS版本: {version}",
            f"  当前密码套件: {current_cipher[0]}",
            f"  密钥强度: {current_cipher[1]} 位",
        ]
        
        # 检查当前密码套件是否弱
        cipher_name = current_cipher[0].upper()
        weak_found = [w for w in WEAK_CIPHERS if w in cipher_name]
        if weak_found:
            result.append(f"  ⚠️ 警告: 当前使用弱密码套件 ({', '.join(weak_found)})")
        else:
            result.append(f"  ✅ 当前密码套件安全")
        
        # 检查是否支持前向保密
        if 'DHE' in cipher_name or 'ECDHE' in cipher_name:
            result.append(f"  ✅ 支持前向保密 (DHE/ECDHE)")
        else:
            result.append(f"  ⚠️ 不支持前向保密")
        
        return "\n".join(result)
    
    except Exception as e:
        return f"密码套件检查失败: {e}"


def _ssl_protocol_check(host: str, port: int = 443, **kwargs) -> str:
    """检查SSL/TLS协议版本支持情况"""
    protocols = {
        'SSLv2': (ssl.PROTOCOL_SSLv2 if hasattr(ssl, 'PROTOCOL_SSLv2') else None, True),
        'SSLv3': (ssl.PROTOCOL_SSLv3 if hasattr(ssl, 'PROTOCOL_SSLv3') else None, True),
        'TLSv1.0': (ssl.PROTOCOL_TLSv1 if hasattr(ssl, 'PROTOCOL_TLSv1') else None, False),
        'TLSv1.1': (ssl.PROTOCOL_TLSv1_1 if hasattr(ssl, 'PROTOCOL_TLSv1_1') else None, False),
        'TLSv1.2': (ssl.PROTOCOL_TLSv1_2 if hasattr(ssl, 'PROTOCOL_TLSv1_2') else None, False),
        'TLSv1.3': (ssl.PROTOCOL_TLSv1_3 if hasattr(ssl, 'PROTOCOL_TLSv1_3') else None, False),
    }
    
    result = [f"✅ SSL/TLS 协议版本检查: {host}:{port}"]
    
    for proto_name, (proto_const, is_insecure) in protocols.items():
        if proto_const is None:
            result.append(f"  {proto_name}: 当前环境不支持检测")
            continue
        
        try:
            context = ssl.SSLContext(proto_const)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            
            with socket.create_connection((host, port), timeout=5) as sock:
                with context.wrap_socket(sock, server_hostname=host) as ssock:
                    actual_version = ssock.version()
                    status = "✅ 支持" if actual_version else "❌ 不支持"
                    if is_insecure:
                        status += " ⚠️ 不安全协议"
                    result.append(f"  {proto_name}: {status}")
        except ssl.SSLError:
            result.append(f"  {proto_name}: ❌ 不支持")
        except socket.timeout:
            result.append(f"  {proto_name}: ⏱ 超时")
        except Exception as e:
            result.append(f"  {proto_name}: ❌ {str(e)[:30]}")
    
    # 安全建议
    result.append(f"\n  📋 安全建议:")
    result.append(f"  - 应禁用: SSLv2, SSLv3, TLSv1.0, TLSv1.1")
    result.append(f"  - 推荐启用: TLSv1.2, TLSv1.3")
    
    return "\n".join(result)


# ═══════════════════════════════════════════════════════════════
# HTTP 安全检测
# ═══════════════════════════════════════════════════════════════

def _security_headers_check(url: str, **kwargs) -> str:
    """检查HTTP安全响应头"""
    try:
        import requests as req
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        resp = req.get(url, headers=headers, timeout=15, allow_redirects=True)
        
        security_headers = {
            "Strict-Transport-Security": {
                "desc": "HTTP严格传输安全(HSTS)",
                "good": True,
                "check": lambda v: "max-age" in v.lower()
            },
            "Content-Security-Policy": {
                "desc": "内容安全策略(CSP)",
                "good": True,
                "check": lambda v: True
            },
            "X-Frame-Options": {
                "desc": "点击劫持保护",
                "good": True,
                "check": lambda v: v.upper() in ("DENY", "SAMEORIGIN")
            },
            "X-Content-Type-Options": {
                "desc": "MIME类型嗅探保护",
                "good": True,
                "check": lambda v: v.lower() == "nosniff"
            },
            "X-XSS-Protection": {
                "desc": "XSS过滤",
                "good": True,
                "check": lambda v: "1" in v
            },
            "Referrer-Policy": {
                "desc": "引用策略",
                "good": True,
                "check": lambda v: True
            },
            "Permissions-Policy": {
                "desc": "权限策略",
                "good": True,
                "check": lambda v: True
            },
            "Access-Control-Allow-Origin": {
                "desc": "CORS跨域配置",
                "good": False,
                "check": lambda v: v != "*"
            },
            "Server": {
                "desc": "服务器信息泄露",
                "good": False,
                "check": lambda v: False  # 存在即信息泄露
            },
            "X-Powered-By": {
                "desc": "技术栈信息泄露",
                "good": False,
                "check": lambda v: False
            },
        }
        
        result = [f"✅ HTTP安全响应头检查: {url}"]
        result.append(f"  状态码: {resp.status_code}")
        result.append(f"  URL: {resp.url}")
        result.append("")
        
        found_count = 0
        missing_count = 0
        warning_count = 0
        
        for header, info in security_headers.items():
            value = resp.headers.get(header)
            if value:
                found_count += 1
                is_good = info["good"]
                check_pass = info["check"](value)
                
                if is_good and check_pass:
                    result.append(f"  ✅ {info['desc']} ({header})")
                elif is_good and not check_pass:
                    warning_count += 1
                    result.append(f"  ⚠️ {info['desc']} ({header}): 配置可能不正确 - {value}")
                elif not is_good:
                    warning_count += 1
                    result.append(f"  ⚠️ {info['desc']} ({header}): {value}")
            else:
                if info["good"]:
                    missing_count += 1
                    result.append(f"  ❌ {info['desc']} ({header}): 缺失")
        
        result.append("")
        result.append(f"  📊 统计: 已配置 {found_count} 项 | 缺失 {missing_count} 项 | 警告 {warning_count} 项")
        
        if missing_count > 0:
            result.append(f"\n  📋 建议添加的安全头:")
            if "Strict-Transport-Security" not in resp.headers:
                result.append(f"    Strict-Transport-Security: max-age=31536000; includeSubDomains")
            if "Content-Security-Policy" not in resp.headers:
                result.append(f"    Content-Security-Policy: default-src 'self'")
            if "X-Frame-Options" not in resp.headers:
                result.append(f"    X-Frame-Options: DENY")
            if "X-Content-Type-Options" not in resp.headers:
                result.append(f"    X-Content-Type-Options: nosniff")
        
        return "\n".join(result)
    
    except ImportError:
        return "HTTP安全头检查需要 requests 库 (pip install requests)"
    except Exception as e:
        return f"HTTP安全头检查失败: {e}"


def _http_security_scan(url: str, **kwargs) -> str:
    """HTTP安全扫描"""
    try:
        import requests as req
        
        parsed = urlparse(url)
        base_host = parsed.netloc or parsed.hostname
        scheme = parsed.scheme or "http"
        
        if not base_host:
            return f"无效的URL: {url}"
        
        result = [f"✅ HTTP安全扫描: {url}"]
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        # 1. 检查HTTPS
        if scheme == "http":
            https_url = f"https://{base_host}/"
            try:
                resp_https = req.get(https_url, headers=headers, timeout=10, allow_redirects=False)
                if resp_https.status_code < 400:
                    result.append(f"  ✅ HTTPS可用: {https_url}")
                    # 检查是否自动跳转HTTPS
                    if resp_https.status_code in (301, 302, 307, 308):
                        result.append(f"  ✅ HTTP自动跳转到HTTPS")
                    else:
                        result.append(f"  ⚠️ HTTPS可用但未配置自动跳转")
                else:
                    result.append(f"  ❌ HTTPS不可用 (状态码: {resp_https.status_code})")
            except Exception:
                result.append(f"  ❌ HTTPS不可用")
        else:
            result.append(f"  ✅ 已使用HTTPS")
        
        # 2. 检查HTTP方法
        try:
            test_methods = ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH", "TRACE"]
            allowed_methods = []
            for method in test_methods:
                try:
                    r = req.request(method, url, headers=headers, timeout=5)
                    if r.status_code not in (405, 501, 403):
                        allowed_methods.append(method)
                except Exception:
                    continue
            
            dangerous = [m for m in allowed_methods if m in ("PUT", "DELETE", "TRACE", "PATCH")]
            if dangerous:
                result.append(f"  ⚠️ 启用了危险HTTP方法: {', '.join(dangerous)}")
            else:
                result.append(f"  ✅ HTTP方法安全 (仅允许: {', '.join(allowed_methods)})")
        except Exception:
            pass
        
        # 3. 检查CORS
        try:
            cors_headers = {
                "Origin": "https://evil.com",
                "User-Agent": "Mozilla/5.0"
            }
            r = req.get(url, headers=cors_headers, timeout=5)
            acao = r.headers.get("Access-Control-Allow-Origin", "")
            if acao == "*":
                result.append(f"  ⚠️ CORS配置过于宽松: Access-Control-Allow-Origin: *")
            elif acao:
                result.append(f"  ℹ️ CORS配置: {acao}")
            else:
                result.append(f"  ✅ 未配置CORS (默认安全)")
        except Exception:
            pass
        
        # 4. 检查安全头
        result.append("")
        result.append(_security_headers_check(url))
        
        return "\n".join(result)
    
    except ImportError:
        return "HTTP安全扫描需要 requests 库 (pip install requests)"
    except Exception as e:
        return f"HTTP安全扫描失败: {e}"


def _check_open_relay(host: str, port: int = 25, **kwargs) -> str:
    """检查SMTP开放中继"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect((host, port))
        
        # 接收 banner
        banner = sock.recv(1024).decode('utf-8', errors='ignore')
        
        # 尝试发送测试邮件（不实际发送）
        import random
        test_from = f"test{random.randint(10000, 99999)}@example.com"
        test_to = f"test{random.randint(10000, 99999)}@test.com"
        
        commands = [
            f"EHLO security-check.local\r\n",
            f"MAIL FROM:<{test_from}>\r\n",
            f"RCPT TO:<{test_to}>\r\n",
            "QUIT\r\n"
        ]
        
        responses = []
        for cmd in commands:
            sock.send(cmd.encode())
            time.sleep(0.3)
            try:
                resp = sock.recv(1024).decode('utf-8', errors='ignore')
                responses.append(resp.strip())
            except socket.timeout:
                responses.append("(超时)")
        
        sock.close()
        
        result = [f"✅ SMTP开放中继检查: {host}:{port}"]
        result.append(f"  Banner: {banner.strip()[:100]}")
        
        for i, (cmd, resp) in enumerate(zip(commands, responses)):
            cmd_clean = cmd.strip()
            result.append(f"  >> {cmd_clean}")
            result.append(f"  << {resp[:100]}")
        
        # 判断是否为开放中继
        if len(responses) >= 3:
            rcpt_response = responses[2]
            if rcpt_response.startswith("250") or rcpt_response.startswith("2"):
                result.append(f"\n  ⚠️ 警告: 服务器可能配置为开放中继！")
                result.append(f"  建议: 立即检查邮件服务器配置，限制中继转发。")
            elif rcpt_response.startswith("550") or rcpt_response.startswith("5"):
                result.append(f"\n  ✅ 服务器非开放中继 (已拒绝外部中继)")
            elif rcpt_response.startswith("450") or rcpt_response.startswith("4"):
                result.append(f"\n  ℹ️ 服务器需要认证 (非开放中继)")
            else:
                result.append(f"\n  ℹ️ 无法确定中继状态")
        
        return "\n".join(result)
    
    except socket.timeout:
        return f"连接超时: {host}:{port}"
    except ConnectionRefusedError:
        return f"连接被拒绝: {host}:{port} (SMTP服务未运行)"
    except Exception as e:
        return f"SMTP检查失败: {e}"


# ═══════════════════════════════════════════════════════════════
# 哈希计算
# ═══════════════════════════════════════════════════════════════

def _hash_file(file_path: str, algorithm: str = "sha256", **kwargs) -> str:
    """计算文件哈希值"""
    path = Path(file_path)
    if not path.exists():
        return f"文件不存在: {file_path}"
    if not path.is_file():
        return f"路径不是文件: {file_path}"
    
    algorithm = algorithm.lower().replace("-", "").replace("_", "")
    
    # 映射算法名称到hashlib
    algo_map = {
        "md5": hashlib.md5,
        "sha1": hashlib.sha1,
        "sha256": hashlib.sha256,
        "sha512": hashlib.sha512,
        "sha3224": lambda: hashlib.new("sha3_224"),
        "sha3256": lambda: hashlib.new("sha3_256"),
        "sha3512": lambda: hashlib.new("sha3_512"),
        "blake2b": lambda: hashlib.blake2b(),
        "blake2s": lambda: hashlib.blake2s(),
    }
    
    if algorithm not in algo_map:
        return (f"不支持的算法: {algorithm}\n"
                f"支持的算法: {', '.join(algo_map.keys())}")
    
    try:
        h = algo_map[algorithm]()
        file_size = path.stat().st_size
        
        with open(path, 'rb') as f:
            # 大文件分块读取
            while True:
                chunk = f.read(65536)  # 64KB
                if not chunk:
                    break
                h.update(chunk)
        
        hash_value = h.hexdigest()
        
        return (f"✅ 文件哈希计算完成\n"
                f"  文件: {file_path}\n"
                f"  大小: {file_size:,} 字节\n"
                f"  算法: {algorithm.upper()}\n"
                f"  哈希值: {hash_value}")
    
    except Exception as e:
        return f"哈希计算失败: {e}"


def _hash_text(text: str, algorithm: str = "sha256", key: str = None, **kwargs) -> str:
    """计算文本哈希值"""
    algorithm = algorithm.lower()
    
    try:
        if algorithm == "hmac":
            if not key:
                return "HMAC需要提供密钥 (key参数)"
            h = hmac.new(key.encode('utf-8'), text.encode('utf-8'), hashlib.sha256)
            return (f"✅ HMAC-SHA256 计算完成\n"
                    f"  文本长度: {len(text)} 字符\n"
                    f"  密钥: {key[:10]}...\n"
                    f"  HMAC: {h.hexdigest()}")
        
        algo_map = {
            "md5": hashlib.md5,
            "sha1": hashlib.sha1,
            "sha256": hashlib.sha256,
            "sha512": hashlib.sha512,
        }
        
        if algorithm not in algo_map:
            return (f"不支持的算法: {algorithm}\n"
                    f"支持的算法: {', '.join(algo_map.keys())}, hmac")
        
        h = algo_map[algorithm](text.encode('utf-8'))
        return (f"✅ {algorithm.upper()} 哈希计算完成\n"
                f"  文本长度: {len(text)} 字符\n"
                f"  哈希值: {h.hexdigest()}")
    
    except Exception as e:
        return f"哈希计算失败: {e}"


# ═══════════════════════════════════════════════════════════════
# IP/网络信息工具
# ═══════════════════════════════════════════════════════════════

def _ip_geolocation(ip: str, **kwargs) -> str:
    """IP地理位置查询"""
    try:
        import requests as req
        
        # 使用多个免费IP地理定位API
        apis = [
            f"https://ipapi.co/{ip}/json/",
            f"https://ipinfo.io/{ip}/json",
        ]
        
        for api_url in apis:
            try:
                r = req.get(api_url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code == 200:
                    data = r.json()
                    
                    result = [f"✅ IP地理位置查询: {ip}"]
                    
                    # ipapi.co 格式
                    if "city" in data:
                        result.append(f"  IP: {data.get('ip', ip)}")
                        result.append(f"  城市: {data.get('city', 'N/A')}")
                        result.append(f"  区域: {data.get('region', 'N/A')}")
                        result.append(f"  国家: {data.get('country_name', data.get('country', 'N/A'))}")
                        result.append(f"  邮编: {data.get('postal', 'N/A')}")
                        result.append(f"  经纬度: {data.get('latitude', 'N/A')}, {data.get('longitude', 'N/A')}")
                        result.append(f"  ISP: {data.get('org', data.get('asn', 'N/A'))}")
                        result.append(f"  时区: {data.get('timezone', 'N/A')}")
                        return "\n".join(result)
                    
                    # ipinfo.io 格式
                    if "city" in data:
                        result.append(f"  IP: {data.get('ip', ip)}")
                        result.append(f"  城市: {data.get('city', 'N/A')}")
                        result.append(f"  区域: {data.get('region', 'N/A')}")
                        result.append(f"  国家: {data.get('country', 'N/A')}")
                        result.append(f"  经纬度: {data.get('loc', 'N/A')}")
                        result.append(f"  组织: {data.get('org', 'N/A')}")
                        result.append(f"  邮编: {data.get('postal', 'N/A')}")
                        result.append(f"  时区: {data.get('timezone', 'N/A')}")
                        return "\n".join(result)
                    
                    return f"IP信息: {json.dumps(data, ensure_ascii=False, indent=2)[:1000]}"
            except Exception:
                continue
        
        return f"无法获取 {ip} 的地理位置信息（所有API均失败）"
    
    except ImportError:
        return "IP地理位置查询需要 requests 库 (pip install requests)"
    except Exception as e:
        return f"IP地理位置查询失败: {e}"


def _subnet_calculator(network: str, **kwargs) -> str:
    """子网计算器"""
    try:
        net = ipaddress.ip_network(network, strict=False)
        
        result = [f"✅ 子网计算: {network}"]
        result.append(f"  网络地址: {net.network_address}")
        result.append(f"  广播地址: {net.broadcast_address}")
        result.append(f"  子网掩码: {net.netmask}")
        result.append(f"  通配符掩码: {net.hostmask}")
        result.append(f"  CIDR前缀: /{net.prefixlen}")
        result.append(f"  地址总数: {net.num_addresses:,}")
        
        if net.num_addresses <= 65536:
            usable = list(net.hosts())
            result.append(f"  可用主机数: {len(usable):,}")
            if usable:
                result.append(f"  可用范围: {usable[0]} ~ {usable[-1]}")
        else:
            result.append(f"  可用主机数: {net.num_addresses - 2:,}")
            result.append(f"  (网络过大，不显示具体范围)")
        
        # IP类型检测
        if net.is_private:
            result.append(f"  🏠 私有网络 (RFC 1918)")
        if net.is_loopback:
            result.append(f"  🔁 回环地址")
        if net.is_multicast:
            result.append(f"  📡 组播地址")
        if net.is_link_local:
            result.append(f"  🔗 链路本地地址")
        if net.is_global and not net.is_private:
            result.append(f"  🌐 公网地址")
        
        return "\n".join(result)
    
    except ValueError as e:
        return f"无效的网络地址: {e}"
    except Exception as e:
        return f"子网计算失败: {e}"


# ═══════════════════════════════════════════════════════════════
# MAC地址查询
# ═══════════════════════════════════════════════════════════════

# 常见MAC前缀厂商映射（前24位/OUI）
MAC_VENDORS = {
    "00:00:0C": "Cisco Systems",
    "00:01:2E": "IBM",
    "00:03:93": "Apple",
    "00:05:69": "Huawei Technologies",
    "00:0C:29": "VMware",
    "00:14:22": "Dell",
    "00:15:5D": "Microsoft",
    "00:1A:11": "Google",
    "00:1B:21": "Intel Corporate",
    "00:1E:68": "Samsung Electronics",
    "00:1F:5B": "HP",
    "00:21:5C": "Cisco Systems",
    "00:22:68": "Xiaomi Communications",
    "00:23:32": "ASUSTek Computer",
    "00:24:1E": "Lenovo",
    "00:25:90": "Sony",
    "00:26:AB": "Apple",
    "00:50:56": "VMware",
    "00:50:79": "Microsoft",
    "00:60:2F": "Intel",
    "00:A0:C9": "Intel",
    "08:00:20": "Sun Microsystems",
    "08:00:27": "Oracle VirtualBox",
    "08:00:46": "Sony",
    "08:74:02": "Huawei Technologies",
    "0C:9D:92": "Huawei Technologies",
    "10:62:E5": "Xiaomi Communications",
    "14:10:9F": "Dell",
    "14:58:D0": "Huawei Technologies",
    "18:66:DA": "Samsung Electronics",
    "1C:69:7A": "Cisco Systems",
    "20:68:7D": "Huawei Technologies",
    "24:4B:FE": "Samsung Electronics",
    "28:16:2E": "Huawei Technologies",
    "28:D2:44": "Apple",
    "2C:54:91": "Huawei Technologies",
    "30:3A:64": "Apple",
    "34:9B:5B": "Huawei Technologies",
    "38:F9:D3": "Xiaomi Communications",
    "3C:07:54": "Huawei Technologies",
    "3C:22:FB": "Dell",
    "40:8D:5C": "Huawei Technologies",
    "44:38:39": "Cisco Systems",
    "44:D9:E7": "Raspberry Pi",
    "48:8E:9C": "Xiaomi Communications",
    "4C:77:6B": "Dell",
    "50:3E:AA": "Cisco Systems",
    "50:76:AF": "Huawei Technologies",
    "54:27:1E": "Huawei Technologies",
    "54:8C:A0": "Samsung Electronics",
    "58:CB:52": "Dell",
    "5C:CF:7F": "Cisco Systems",
    "60:57:18": "Apple",
    "64:09:80": "Huawei Technologies",
    "64:6B:F0": "Xiaomi Communications",
    "68:05:CA": "HP",
    "6C:2E:85": "Huawei Technologies",
    "6C:3B:6B": "Huawei Technologies",
    "70:4C:A5": "Apple",
    "70:8B:CD": "ASUSTek Computer",
    "74:4D:28": "Raspberry Pi",
    "78:2B:CB": "HP",
    "7C:DD:90": "Huawei Technologies",
    "80:2A:A8": "Huawei Technologies",
    "84:16:F9": "Microsoft",
    "84:7B:3B": "Xiaomi Communications",
    "88:53:2E": "Huawei Technologies",
    "8C:85:90": "Intel",
    "90:9A:4A": "Huawei Technologies",
    "94:65:2D": "Xiaomi Communications",
    "98:90:96": "ASUSTek Computer",
    "9C:2E:A1": "Huawei Technologies",
    "A0:36:9F": "Huawei Technologies",
    "A4:45:19": "Intel",
    "A8:1E:84": "Xiaomi Communications",
    "AC:1F:6B": "Dell",
    "B0:4E:26": "Cisco Systems",
    "B4:2E:E4": "Huawei Technologies",
    "B8:27:EB": "Raspberry Pi",
    "BC:5F:F4": "ASUSTek Computer",
    "C0:3F:0E": "Huawei Technologies",
    "C4:93:00": "Intel",
    "C8:5B:76": "Huawei Technologies",
    "CC:2D:8C": "Huawei Technologies",
    "D0:17:C2": "Intel",
    "D4:6A:91": "Huawei Technologies",
    "D8:3A:DD": "Samsung Electronics",
    "DC:A6:32": "Intel",
    "E0:2C:C6": "Huawei Technologies",
    "E0:9D:31": "Xiaomi Communications",
    "E4:70:B8": "Huawei Technologies",
    "E8:48:B8": "Xiaomi Communications",
    "EC:1A:59": "Huawei Technologies",
    "F0:18:98": "Huawei Technologies",
    "F4:6D:04": "Dell",
    "F8:5E:A0": "Xiaomi Communications",
    "FC:AA:14": "Huawei Technologies",
}


def _mac_address_lookup(mac: str, **kwargs) -> str:
    """MAC地址厂商查询"""
    # 标准化MAC地址
    mac_clean = mac.upper().replace("-", ":").replace(".", ":")
    
    # 验证格式
    if not re.match(r'^([0-9A-F]{2}:){5}[0-9A-F]{2}$', mac_clean):
        # 尝试补全格式
        mac_digits = re.sub(r'[^0-9A-F]', '', mac_clean)
        if len(mac_digits) == 12:
            mac_clean = ":".join(mac_digits[i:i+2] for i in range(0, 12, 2))
        else:
            return f"无效的MAC地址格式: {mac}\n正确格式: 00:1A:2B:3C:4D:5E"
    
    # 提取OUI（前3个字节）
    oui = mac_clean[:8]  # XX:XX:XX
    
    # 判断MAC类型
    first_byte = int(mac_clean.split(':')[0], 16)
    is_unicast = (first_byte & 0x01) == 0
    is_global = (first_byte & 0x02) == 0
    
    # 查询厂商
    vendor = MAC_VENDORS.get(oui, "未知厂商")
    
    result = [
        f"✅ MAC地址查询: {mac_clean}",
        f"  OUI (厂商ID): {oui}",
        f"  设备厂商: {vendor}",
    ]
    
    if not is_unicast:
        result.append(f"  📡 类型: 组播MAC地址")
    elif not is_global:
        result.append(f"  🔒 类型: 本地管理地址 (LAA)")
    else:
        result.append(f"  🌐 类型: 全球唯一地址 (UAA)")
    
    # 常见虚拟化平台识别
    virtual_ouis = {
        "00:05:69": "VMware",
        "00:0C:29": "VMware",
        "00:50:56": "VMware",
        "08:00:27": "VirtualBox",
        "00:15:5D": "Hyper-V",
        "00:1C:42": "Parallels",
        "00:50:56": "VMware ESX",
        "52:54:00": "QEMU/KVM",
    }
    
    if oui in virtual_ouis:
        result.append(f"  💻 虚拟化平台: {virtual_ouis[oui]}")
    
    return "\n".join(result)


# ═══════════════════════════════════════════════════════════════
# WHOIS / 证书透明度
# ═══════════════════════════════════════════════════════════════

def _whois_lookup(domain: str, **kwargs) -> str:
    """WHOIS域名信息查询"""
    try:
        import whois
        w = whois.whois(domain)
        
        result = [f"✅ WHOIS查询: {domain}"]
        
        if w.domain_name:
            names = w.domain_name if isinstance(w.domain_name, list) else [w.domain_name]
            result.append(f"  域名: {', '.join(names)}")
        
        if w.registrar:
            result.append(f"  注册商: {w.registrar}")
        
        if w.creation_date:
            dates = w.creation_date if isinstance(w.creation_date, list) else [w.creation_date]
            result.append(f"  创建时间: {dates[0]}")
        
        if w.expiration_date:
            dates = w.expiration_date if isinstance(w.expiration_date, list) else [w.expiration_date]
            result.append(f"  过期时间: {dates[0]}")
            try:
                remaining = (dates[0] - datetime.now()).days
                result.append(f"  剩余天数: {remaining} 天")
                if remaining < 30:
                    result.append(f"  ⚠️ 域名即将过期！")
            except Exception:
                pass
        
        if w.name_servers:
            ns = w.name_servers if isinstance(w.name_servers, list) else [w.name_servers]
            result.append(f"  DNS服务器:")
            for n in ns[:5]:
                result.append(f"    {n}")
        
        if w.org:
            result.append(f"  组织: {w.org}")
        
        if w.country:
            result.append(f"  国家: {w.country}")
        
        if w.status:
            statuses = w.status if isinstance(w.status, list) else [w.status]
            result.append(f"  状态: {', '.join(statuses[:3])}")
        
        if w.dnssec:
            result.append(f"  DNSSEC: {w.dnssec}")
        
        return "\n".join(result)
    
    except ImportError:
        return "WHOIS查询需要 python-whois 库 (pip install python-whois)"
    except Exception as e:
        return f"WHOIS查询失败: {e}"


def _certificate_transparency(domain: str, **kwargs) -> str:
    """证书透明度日志查询"""
    try:
        import requests as req
        
        # 使用 crt.sh 的免费API
        url = f"https://crt.sh/?q={domain}&output=json"
        r = req.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        
        if r.status_code != 200:
            return f"证书透明度查询失败 (HTTP {r.status_code})"
        
        certs = r.json()
        
        if not certs:
            return f"未在证书透明度日志中找到 {domain} 的SSL证书记录"
        
        # 去重并提取关键信息
        seen = set()
        unique_certs = []
        for cert in certs:
            cert_id = cert.get('id', '')
            if cert_id not in seen:
                seen.add(cert_id)
                unique_certs.append(cert)
        
        unique_certs = unique_certs[:20]  # 最多显示20条
        
        result = [
            f"✅ 证书透明度日志查询: {domain}",
            f"  找到 {len(unique_certs)} 条SSL证书记录 (共 {len(certs)} 条)",
        ]
        
        for cert in unique_certs:
            name = cert.get('common_name', 'N/A')
            issuer = cert.get('issuer_name', 'N/A')[:60]
            not_before = cert.get('not_before', 'N/A')[:10]
            not_after = cert.get('not_after', 'N/A')[:10]
            
            result.append(f"\n  证书 #{cert.get('id', 'N/A')}")
            result.append(f"    域名: {name}")
            result.append(f"    颁发者: {issuer}")
            result.append(f"    有效期: {not_before} ~ {not_after}")
        
        if len(unique_certs) < len(certs):
            result.append(f"\n  ... 还有 {len(certs) - len(unique_certs)} 条记录未显示")
        
        return "\n".join(result)
    
    except ImportError:
        return "证书透明度查询需要 requests 库 (pip install requests)"
    except Exception as e:
        return f"证书透明度查询失败: {e}"


# ═══════════════════════════════════════════════════════════════
# 编码/解码工具
# ═══════════════════════════════════════════════════════════════

def _base64_encode(text: str, **kwargs) -> str:
    """Base64编码"""
    try:
        encoded = base64.b64encode(text.encode('utf-8')).decode('utf-8')
        return f"✅ Base64编码完成\n  原文: {text[:100]}{'...' if len(text) > 100 else ''}\n  编码: {encoded}"
    except Exception as e:
        return f"Base64编码失败: {e}"


def _base64_decode(encoded: str, **kwargs) -> str:
    """Base64解码"""
    try:
        decoded = base64.b64decode(encoded).decode('utf-8', errors='replace')
        return f"✅ Base64解码完成\n  编码: {encoded[:100]}{'...' if len(encoded) > 100 else ''}\n  原文: {decoded[:500]}{'...' if len(decoded) > 500 else ''}"
    except Exception as e:
        return f"Base64解码失败: {e}\n请确认输入是有效的Base64编码字符串"


def _hex_encode(text: str, **kwargs) -> str:
    """十六进制编码"""
    try:
        encoded = text.encode('utf-8').hex()
        return f"✅ Hex编码完成\n  原文: {text[:100]}{'...' if len(text) > 100 else ''}\n  编码: {encoded}"
    except Exception as e:
        return f"Hex编码失败: {e}"


def _hex_decode(encoded: str, **kwargs) -> str:
    """十六进制解码"""
    try:
        # 移除可能的 0x 前缀和空格
        clean = encoded.replace(' ', '').replace('0x', '').replace('0X', '')
        decoded = bytes.fromhex(clean).decode('utf-8', errors='replace')
        return f"✅ Hex解码完成\n  编码: {encoded[:100]}{'...' if len(encoded) > 100 else ''}\n  原文: {decoded[:500]}{'...' if len(decoded) > 500 else ''}"
    except ValueError as e:
        return f"Hex解码失败: 无效的十六进制字符串 - {e}"
    except Exception as e:
        return f"Hex解码失败: {e}"
