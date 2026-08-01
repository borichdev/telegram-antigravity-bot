import asyncio
import json
import logging
from typing import AsyncGenerator, Dict, Any, Optional
import config

logger = logging.getLogger(__name__)

class AntigravityRunner:
    def __init__(self):
        self.active_processes: Dict[int, asyncio.subprocess.Process] = {}

    async def cancel_for_user(self, user_id: int):
        process = self.active_processes.get(user_id)
        if process and process.returncode is None:
            try:
                process.terminate()
                await asyncio.sleep(0.5)
                if process.returncode is None:
                    process.kill()
            except Exception as e:
                logger.error(f"Error terminating process for user {user_id}: {e}")
            finally:
                self.active_processes.pop(user_id, None)

    async def run_prompt_stream(
        self,
        user_id: int,
        prompt: str,
        conversation_id: Optional[str] = None,
        workspace_dir: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        cmd = [
            config.AGY_PATH,
            "--prompt", prompt,
            "--output-format", "stream-json",
            "--dangerously-skip-permissions"
        ]

        if conversation_id:
            cmd.extend(["--conversation", conversation_id])

        if config.DEFAULT_MODEL:
            cmd.extend(["--model", config.DEFAULT_MODEL])

        cwd = workspace_dir or config.WORKSPACE_DIR

        logger.info(f"Executing agy for user {user_id} in {cwd}: {' '.join(cmd)}")

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd
        )

        self.active_processes[user_id] = process

        try:
            current_conv_id = conversation_id

            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                line_str = line.decode('utf-8', errors='replace').strip()
                if not line_str:
                    continue

                try:
                    data = json.loads(line_str)
                    event_type = data.get("event")

                    if event_type == "init":
                        current_conv_id = data.get("conversation_id")
                        yield {
                            "type": "init",
                            "conversation_id": current_conv_id
                        }

                    elif event_type == "step_update":
                        update = data.get("step_update", {})
                        step_type = update.get("step_type")
                        text_delta = update.get("text_delta", "")

                        yield {
                            "type": "update",
                            "step_type": step_type,
                            "text_delta": text_delta,
                            "conversation_id": current_conv_id
                        }

                    elif event_type == "result":
                        res = data.get("result", {})
                        current_conv_id = res.get("conversation_id", current_conv_id)
                        final_response = res.get("response", "")
                        status = res.get("status", "SUCCESS")
                        yield {
                            "type": "result",
                            "conversation_id": current_conv_id,
                            "response": final_response,
                            "status": status
                        }
                except json.JSONDecodeError:
                    logger.warning(f"Raw non-JSON output from agy: {line_str}")
                    yield {
                        "type": "raw",
                        "text": line_str
                    }

            await process.wait()

            if process.returncode != 0:
                stderr_data = await process.stderr.read()
                err_text = stderr_data.decode('utf-8', errors='replace')
                yield {
                    "type": "error",
                    "error": f"Process exited with code {process.returncode}: {err_text}"
                }

        except asyncio.CancelledError:
            await self.cancel_for_user(user_id)
            raise
        except Exception as e:
            logger.error(f"Error in agy stream execution: {e}", exc_info=True)
            yield {
                "type": "error",
                "error": str(e)
            }
        finally:
            self.active_processes.pop(user_id, None)
