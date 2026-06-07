    def _run_stream_loop(self, think_mode: bool = False, use_override: bool = False) -> Generator:
        """统一流式循环"""
        set_current_identity(self.identity)

        for iteration in range(MAX_ITERATIONS):
            full_content = ""
            accumulated_reasoning = ""
            tool_calls_accumulator = {}
            finish_reason = None
            chunk_count = 0
            last_save_time = time.time()

            base_url = None
            if use_override and self._override_messages_for_next_call is not None:
                messages_for_api = self._override_messages_for_next_call
                base_url = self._override_base_url_for_next_call
            else:
                messages_for_api = self._prepare_messages_for_api(think_mode)

            for chunk in self._call_deepseek_stream(messages_for_api, think_mode, base_url=base_url):
                if "error" in chunk:
                    if full_content or accumulated_reasoning:
                        self._save_partial_response(full_content, accumulated_reasoning, think_mode)
                    yield f"data: {json.dumps({'type': 'error', 'error': chunk.get('error', str(chunk))})}\n\n"
                    self.last_assistant_incomplete = True
                    return
                if "recovery" in chunk:
                    yield f"data: {json.dumps({'type': 'recovery', 'recovery': chunk['recovery']})}\n\n"
                    continue
                if "retry_info" in chunk:
                    yield f"data: {json.dumps({'type': 'retry_info', 'retry_info': chunk['retry_info']})}\n\n"
                    continue

                choice = chunk.get("choices", [{}])[0]
                delta = choice.get("delta", {})
                finish_reason = choice.get("finish_reason", finish_reason)

                if think_mode and delta.get("reasoning_content"):
                    accumulated_reasoning += delta["reasoning_content"]
                    yield f"data: {json.dumps({'type': 'reasoning', 'chunk': delta['reasoning_content']})}\n\n"
                if delta.get("content"):
                    full_content += delta["content"]
                    chunk_count += 1
                    yield f"data: {json.dumps({'type': 'content', 'chunk': delta['content']})}\n\n"
                if "tool_calls" in delta:
                    for tc_delta in delta["tool_calls"]:
                        idx = tc_delta.get("index", 0)
                        if idx not in tool_calls_accumulator:
                            tool_calls_accumulator[idx] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                        current = tool_calls_accumulator[idx]
                        if tc_delta.get("id"): current["id"] = tc_delta["id"]
                        if "function" in tc_delta:
                            if tc_delta["function"].get("name"): current["function"]["name"] = tc_delta["function"]["name"]
                            if tc_delta["function"].get("arguments"): current["function"]["arguments"] += tc_delta["function"]["arguments"]

                if (chunk_count > 0 and chunk_count % 20 == 0) or (time.time() - last_save_time > 15):
                    if full_content or accumulated_reasoning:
                        self._save_partial_response(full_content, accumulated_reasoning, think_mode)
                        last_save_time = time.time()

            assistant_msg = {"role": "assistant", "content": full_content or ""}
            if think_mode: assistant_msg["reasoning_content"] = accumulated_reasoning

            if finish_reason == "tool_calls" and tool_calls_accumulator:
                tool_calls = [tool_calls_accumulator[i] for i in sorted(tool_calls_accumulator.keys())]
                assistant_msg["tool_calls"] = tool_calls
                yield f"data: {json.dumps({'type': 'tool_call', 'calls': [{'id': tc['id'], 'name': tc['function']['name'], 'arguments': tc['function']['arguments']} for tc in tool_calls]})}\n\n"

                self.conversation_history.append(assistant_msg)
                tool_results, need_auth = self._process_tool_calls(tool_calls)

                for tr in tool_results:
                    self.conversation_history.append(tr)
                    yield f"data: {json.dumps({'type': 'tool_result', 'tool_call_id': tr['tool_call_id'], 'result': tr['content'][:500]})}\n\n"

                self._cleanup_streaming_markers()
                self.memory.save_conversation(self.session_id, self.conversation_history)

                if need_auth:
                    self.pending_auth = need_auth
                    tool_call_id, actual_cmd, _, _ = need_auth
                    yield f"data: {json.dumps({'type': 'auth_required', 'tool_call_id': tool_call_id, 'command': actual_cmd})}\n\n"
                    return

                self._override_messages_for_next_call = None
                self._override_base_url_for_next_call = None
                continue

            self.conversation_history.append(assistant_msg)
            self._cleanup_streaming_markers()
            self.memory.save_conversation(self.session_id, self.conversation_history)
            self.last_assistant_incomplete = False
            return

        yield f"data: {json.dumps({'type': 'error', 'error': '已达到最大迭代次数，请简化任务或手动干预。'})}\n\n"
        yield "data: [DONE]\n\n"
