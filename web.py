# web.py
import atexit
import uuid
import json
import time
import logging
from threading import Lock
from flask import Flask, send_file, request, jsonify, Response, stream_with_context
from flask_cors import CORS

from config import SECRET_KEY, WEB_HOST, WEB_PORT, DEFAULT_PROVIDER, DEFAULT_MODEL
from memory import MemorySystem
from session import MHSession
from retry_utils import failure_tracker, is_network_ok
from key_manager import KeyManager

app = Flask(__name__)
app.secret_key = SECRET_KEY
CORS(app)

SESSIONS = {}
SESSION_LOCK = Lock()
ACTIVE_STREAMS = {}
STREAM_LOCK = Lock()
key_manager = None


def _save_all_sessions_on_exit():
    """程序退出时保存所有活跃 session 的对话历史"""
    with SESSION_LOCK:
        for sid, agent in list(SESSIONS.items()):
            try:
                agent._save_memory_safe()
            except Exception:
                pass

atexit.register(_save_all_sessions_on_exit)

# ── 模型提供商初始化 ──
_provider_mgr = None
try:
    from model_provider import create_default_providers
    _provider_mgr = create_default_providers()
    logging.getLogger("MHAgent.Web").info(
        f"Provider: {' '.join(p.name for p in _provider_mgr.providers.values())}"
    )
except Exception as e:
    logging.getLogger("MHAgent.Web").warning(f"Provider 初始化失败: {e}")

logger = logging.getLogger("MHAgent.Web")


def get_or_create_session(session_id: str, provider_id: str = None,
                          model_id: str = None, identity: str = "agent") -> MHSession:
    with SESSION_LOCK:
        if session_id not in SESSIONS:
            api_keys = key_manager.get_api_keys()
            api_key = api_keys.get("DEEPSEEK_API_KEY", "")
            SESSIONS[session_id] = MHSession(
                session_id, api_key=api_key,
                provider_id=provider_id or DEFAULT_PROVIDER,
                model_id=model_id or DEFAULT_MODEL,
                identity=identity,
                meiju_phone=api_keys.get("MEIJU_PHONE"),
                meiju_password=api_keys.get("MEIJU_PASSWORD"),
            )
        return SESSIONS[session_id]


@app.route('/')
def index():
    return send_file('index.html')


@app.route('/api/network-status', methods=['GET'])
def network_status():
    net_ok = is_network_ok()
    return jsonify({"online": net_ok, "paused": failure_tracker.paused, "consecutive_failures": failure_tracker.consecutive_failures, "pause_reason": failure_tracker.pause_reason})


@app.route('/api/resume', methods=['POST'])
def resume_agent():
    if is_network_ok():
        failure_tracker.reset()
        return jsonify({"success": True, "message": "Agent 已恢复"})
    return jsonify({"success": False, "message": "网络仍不可用"})


@app.route('/api/sessions', methods=['GET'])
def list_sessions():
    identity = request.args.get('identity', 'agent')
    ms = MemorySystem(identity=identity)
    return jsonify(ms.list_all_sessions())


@app.route('/api/sessions/new', methods=['POST'])
def new_session():
    identity = request.json.get("identity", "agent")
    provider_id = request.json.get("provider_id", DEFAULT_PROVIDER)
    model_id = request.json.get("model_id", DEFAULT_MODEL)
    new_id = str(uuid.uuid4())[:8]
    with SESSION_LOCK:
        api_keys = key_manager.get_api_keys()
        SESSIONS[new_id] = MHSession(new_id, api_key=api_keys.get("DEEPSEEK_API_KEY", ""),
                                      identity=identity,
                                      provider_id=provider_id,
                                      model_id=model_id,
                                      meiju_phone=api_keys.get("MEIJU_PHONE"),
                                      meiju_password=api_keys.get("MEIJU_PASSWORD"))
    return jsonify({"session_id": new_id, "title": "新对话"})


@app.route('/api/sessions/<sid>', methods=['DELETE'])
def delete_session(sid):
    identity = request.args.get('identity', 'agent')
    with SESSION_LOCK:
        if sid in SESSIONS: del SESSIONS[sid]
        ms = MemorySystem(identity=identity)
        ms.delete_conversation(sid)
    return jsonify({"success": True})


@app.route('/api/sessions/<sid>/history', methods=['GET'])
def get_session_history(sid):
    identity = request.args.get('identity', 'agent')
    agent = get_or_create_session(sid, identity=identity)
    return jsonify(agent.conversation_history)


@app.route('/api/sessions/<sid>/switch_mode', methods=['POST'])
def switch_mode(sid):
    data = request.json
    with SESSION_LOCK:
        if sid in SESSIONS:
            SESSIONS[sid].provider_id = data.get('provider_id', DEFAULT_PROVIDER)
            SESSIONS[sid].model_id = data.get('model_id', DEFAULT_MODEL)
            return jsonify({"success": True, "provider": SESSIONS[sid].provider_id, "model": SESSIONS[sid].model_id})
    return jsonify({"success": False, "message": "会话不存在"})


@app.route('/chat/stream', methods=['POST'])
def chat_stream_route():
    data = request.json
    user_msg = data.get('message', '')
    session_id = data.get('session_id') or request.cookies.get('current_session')
    think_mode = data.get('think_mode', False)
    continue_mode = data.get('continue_mode', False)
    auto_drive = data.get('auto_drive', False)
    identity = data.get('identity', 'agent')
    if not session_id:
        # 前端应先创建 session，这里拒绝无 session 的请求
        return jsonify({"error": "缺少 session_id，请先创建会话"}), 400
    with SESSION_LOCK:
        if session_id in SESSIONS:
            SESSIONS[session_id].stop()  # 叫停旧流
    agent = get_or_create_session(session_id, identity=identity)
    stream_id = str(uuid.uuid4())
    with STREAM_LOCK: ACTIVE_STREAMS[stream_id] = True

    def generate():
        heartbeat_interval = 15  # 秒
        try:
            last_heartbeat = time.time()
            for chunk in agent.chat_stream(user_msg, think_mode, continue_mode, auto_drive=auto_drive):
                with STREAM_LOCK:
                    if not ACTIVE_STREAMS.get(stream_id, False):
                        yield f"data: {json.dumps({'type': 'stopped', 'message': '用户中断了输出'})}\n\n"; break
                # 隔一段时间发送心跳注释，保持连接
                now = time.time()
                if now - last_heartbeat > heartbeat_interval:
                    yield ": heartbeat\n\n"
                    last_heartbeat = now
                yield chunk
        except GeneratorExit:
            logger.info(f"Stream {stream_id} 客户端断开连接")
        except Exception as e:
            logger.exception("流式处理出错")
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
        finally:
            with STREAM_LOCK: ACTIVE_STREAMS.pop(stream_id, None)

    response = Response(stream_with_context(generate()), mimetype='text/event-stream')
    response.headers['X-Stream-ID'] = stream_id
    return response


@app.route('/chat/stop', methods=['POST'])
def stop_stream():
    data = request.json
    stream_id = data.get('stream_id')
    if stream_id:
        with STREAM_LOCK:
            if stream_id in ACTIVE_STREAMS:
                ACTIVE_STREAMS[stream_id] = False
                return jsonify({"success": True, "message": "已发送中断信号"})
    return jsonify({"success": False, "message": "未找到对应的流"})


@app.route('/api/sessions/<sid>/regenerate', methods=['POST'])
def regenerate(sid):
    identity = request.json.get('identity', 'agent')
    agent = get_or_create_session(sid, identity=identity)
    def generate():
        for chunk in agent.regenerate_stream(): yield chunk
    return Response(stream_with_context(generate()), mimetype='text/event-stream')


@app.route('/api/sessions/<sid>/continue', methods=['POST'])
def continue_write(sid):
    identity = request.json.get('identity', 'agent')
    agent = get_or_create_session(sid, identity=identity)
    # 调用 chat_stream，传递 continue_mode=True（不附加用户文本）
    def generate():
        for chunk in agent.chat_stream(user_input="", think_mode=False, continue_mode=True):
            yield chunk
    return Response(stream_with_context(generate()), mimetype='text/event-stream')


@app.route('/api/sessions/<sid>/rollback', methods=['POST'])
def rollback_session(sid):
    identity = request.json.get('identity', 'agent')
    agent = get_or_create_session(sid, identity=identity)
    history = agent.conversation_history
    last_assistant_idx = -1
    for i in range(len(history) - 1, -1, -1):
        if history[i].get('role') == 'assistant': last_assistant_idx = i; break
    if last_assistant_idx != -1:
        del history[last_assistant_idx:]
        agent.memory.save_conversation(sid, history)
        return jsonify({"success": True, "message": "已回滚到上一轮用户消息"})
    return jsonify({"success": False, "message": "没有可回滚的助手消息"})


@app.route('/api/sessions/<sid>/ai_fix', methods=['POST'])
def ai_fix_session(sid):
    """使用 AI 修复当前会话的消息格式错误"""
    identity = request.json.get('identity', 'agent')
    agent = get_or_create_session(sid, identity=identity)
    history = agent.conversation_history
    repaired = agent._ai_repair_messages(history, "HTTP 400 format error")
    if repaired and repaired != history:
        agent.conversation_history = repaired
        agent.memory.save_conversation(sid, repaired)
        return jsonify({"success": True, "message": "AI 修复完成，请重新发送消息"})
    return jsonify({"success": False, "message": "AI 修复未能识别有效修改"})


@app.route('/api/models', methods=['GET'])
def list_models():
    """获取可用的模型提供商和模型列表"""
    if not _provider_mgr:
        return jsonify({"error": "Provider 未初始化"}), 500
    return jsonify({"providers": _provider_mgr.list_providers()})


@app.route('/api/sessions/<sid>/emergency_restart', methods=['POST'])
def emergency_restart_session(sid):
    """紧急摘要重启 — 用户手动触发，重建干净上下文"""
    identity = request.json.get('identity', 'agent')
    agent = get_or_create_session(sid, identity=identity)
    if agent._emergency_summarize_and_restart():
        return jsonify({"success": True, "message": "上下文已重建，请重新发送消息继续"})
    return jsonify({"success": False, "message": "紧急重启失败，请检查 API Key 和网络"})


@app.route('/api/sessions/<sid>/edit_message', methods=['POST'])
def edit_message(sid):
    data = request.json
    msg_index = data.get('index')
    new_content = data.get('content')
    identity = data.get('identity', 'agent')
    if msg_index is None or new_content is None: return jsonify({"success": False, "message": "缺少参数"}), 400
    agent = get_or_create_session(sid, identity=identity)
    history = agent.conversation_history
    if msg_index < 0 or msg_index >= len(history): return jsonify({"success": False, "message": "索引超出范围"}), 400
    if history[msg_index].get('role') != 'user': return jsonify({"success": False, "message": "指定消息不是用户消息"}), 400
    del history[msg_index:]
    history.append({"role": "user", "content": new_content})
    agent.memory.save_conversation(sid, history)
    return jsonify({"success": True, "message": "消息已修改，可以重新生成回复"})


@app.route('/clear_memory', methods=['POST'])
def clear_memory():
    sid = request.json.get('session_id')
    identity = request.json.get('identity', 'agent')
    if sid:
        ms = MemorySystem(identity=identity)
        ms.delete_conversation(sid)
        if sid in SESSIONS: del SESSIONS[sid]
        return jsonify({"success": True})
    return jsonify({"success": False}), 400






# ==================== API 密钥管理 ====================

@app.route('/api/keys/status', methods=['GET'])
def keys_status():
    """查看哪些密钥已配置（不返回实际值）"""
    try:
        keys = key_manager.get_api_keys() if key_manager else {}
        return jsonify({
            "deepseek": bool(keys.get("DEEPSEEK_API_KEY")),
            "bocha": bool(keys.get("BOCHA_SEARCH_API_KEY")),
            "meiju": bool(keys.get("MEIJU_PHONE") and keys.get("MEIJU_PASSWORD")),
            "initialized": key_manager.is_initialized() if key_manager else False
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/keys/save', methods=['POST'])
def keys_save():
    """保存 API 密钥到加密存储"""
    data = request.json or {}
    try:
        if not key_manager:
            return jsonify({"success": False, "error": "密钥管理器未初始化"}), 500
        
        existing = key_manager.get_api_keys() if key_manager.is_initialized() else {}
        
        if data.get("deepseek_key"):
            existing["DEEPSEEK_API_KEY"] = data["deepseek_key"].strip()
        if data.get("bocha_key"):
            existing["BOCHA_SEARCH_API_KEY"] = data["bocha_key"].strip()
        if data.get("meiju_phone"):
            existing["MEIJU_PHONE"] = data["meiju_phone"].strip()
        if data.get("meiju_password"):
            existing["MEIJU_PASSWORD"] = data["meiju_password"].strip()
        
        key_manager.save_api_keys(existing)
        
        import config
        config.DEEPSEEK_API_KEY = existing.get("DEEPSEEK_API_KEY", "")
        config.BOCHA_SEARCH_API_KEY = existing.get("BOCHA_SEARCH_API_KEY", "")
        
        logger.info("API Keys updated and saved")
        return jsonify({"success": True, "message": "密钥已保存并生效"})
    except Exception as e:
        logger.exception(f"Save keys failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/keys/initialize', methods=['POST'])
def keys_initialize():
    """首次初始化密钥系统"""
    try:
        if not key_manager:
            return jsonify({"success": False, "error": "密钥管理器未就绪"}), 500
        if key_manager.is_initialized():
            return jsonify({"success": True, "message": "密钥系统已初始化", "initialized": True})
        shares = key_manager.initialize()
        return jsonify({"success": True, "message": f"密钥系统已初始化", "initialized": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500