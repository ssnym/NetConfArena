import os
import re
import time
import openai

import re
import base64
import yaml
from string import Template
from copy import deepcopy
from logs.mylogger import logger


class BaseAgent:
    def __init__(self, agent_name):
        self.name = agent_name
        self.llm_model = agent_name.split("___")[1]
        self.llm = ChatLLM(self.llm_model)
        self.trajectory = []
        self.agent_config = ""

    def load_config(self, config_path):
        with open(config_path, 'r', encoding='utf-8') as file:
            self.agent_config = yaml.safe_load(file)
        
        self.system_prompt = self.agent_config['agent']['system_prompt']
        template_values = {}
        if "max_action_turns" in self.agent_config.get("agent", {}):
            template_values["max_action_turns"] = self.agent_config["agent"]["max_action_turns"]
        self.system_prompt = Template(self.system_prompt).safe_substitute(**template_values)
        self.task_prompt_template = Template(self.agent_config['agent']['task_prompt'])

        logger.debug(f"\n--------- System Prompt ---------\n")
        logger.debug(f"{self.system_prompt}")

    @property
    def messages(self):
        """The raw conversation, without the per-message timestamps."""
        return [{"role": m["role"], "content": m["content"]} for m in self.llm.messages]

    def reset(self):
        self.trajectory = []

class OutputParserError(Exception):
    """Exception raised when parsing output from a command fails."""

    def __init__(self, message):
        self.message = message

    def __str__(self):
        return "Failed to parse output of the model:%s\n " % self.message

class ChatLLM():
    def __init__(self, llm_model):
        self.messages = []
        self.TRY_TIME = 3
        self.llm_model = llm_model.split("_no_thinking")[0]
        self.no_thinking = llm_model.endswith("_no_thinking")
        # Endpoint selection, in priority order:
        #   1. LLM_BASE_URL / LLM_API_KEY  — any OpenAI-compatible endpoint,
        #      including a locally served model (vLLM, SGLang, Ollama, ...).
        #      VLLM_SERVER_URL is accepted as an alias for LLM_BASE_URL.
        #   2. neither set — fall back to the OpenAI client defaults, i.e.
        #      OPENAI_API_KEY / OPENAI_BASE_URL from the environment.
        # Locally served models are usually identified by a filesystem path or
        # a HuggingFace repo id; that identifier is passed through unchanged as
        # the `model` field, so no per-model branching is needed here.
        api_kwargs = {}
        base_url = os.environ.get("LLM_BASE_URL") or os.environ.get("VLLM_SERVER_URL")
        if base_url:
            api_kwargs['base_url'] = base_url
            # Self-hosted endpoints usually ignore the key but the client requires one.
            api_kwargs['api_key'] = os.environ.get("LLM_API_KEY", "EMPTY")
        elif os.environ.get("LLM_API_KEY"):
            api_kwargs['api_key'] = os.environ["LLM_API_KEY"]
        self.client = openai.OpenAI(**api_kwargs)


    def _construct_system_message(self, prompt: str):
        cur_time = time.strftime("%H:%M:%S", time.localtime())
        return {"role": "system", "content": prompt, "time": cur_time}

    def _construct_message(self, prompt: str):
        cur_time = time.strftime("%H:%M:%S", time.localtime())
        return {"role": "user", "content": prompt, "time": cur_time}
    
    def _construct_text_and_image_message(self, prompt: str, image_path: str):
        cur_time = time.strftime("%H:%M:%S", time.localtime())
        return {
            "role": "user",
            "content": [
                {
                "type": "text",
                "text": f"{prompt}"
                },
                {"type": "image_url",
                 "image_url": {
                    "url": f"data:image/jpeg;base64,{self.encode_image(image_path)}"
                    }
                },
            ],
            "time": cur_time
            }
    
    def _construct_response_message(self, answer: str):
        cur_time = time.strftime("%H:%M:%S", time.localtime())
        return {"role": "assistant", "content": answer, "time": cur_time}
    
    # Function to encode the image
    def encode_image(self, image_path: str):
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")
        

    def extract_reasoning(self, text: str):
        """Extract and trim content enclosed by `<think>...</think>`."""

        s = str(text)
        start_tag = "<think>"
        end_tag = "</think>"

        # Locate the final opening tag.
        start = s.rfind(start_tag)
        if start == -1:
            return "", text

        # The first closing tag after it terminates the reasoning section.
        end_pair = s.find(end_tag, start + len(start_tag))
        if end_pair == -1:
            return "", text

        # Reasoning is the text between those two tags.
        reasoning = s[start + len(start_tag):end_pair].strip()
        reasoning = re.sub(r'</?think>', '', reasoning).strip()

        # The answer is everything after the final closing tag in the response.
        last_end = s.rfind(end_tag)
        if last_end != -1:
            answer = s[last_end + len(end_tag):].strip()

        return reasoning, answer


    def parse(self):
        streaming = True

        reasoning_content = ""  # Complete reasoning text.
        content = ""  # Complete response, which may include reasoning.
        answer_content = ""  # Final answer without reasoning.

        for i in range(self.TRY_TIME):
            try:
                response = self.client.chat.completions.create(
                    model=self.llm_model,
                    temperature=0.5,
                    messages=self.messages,
                    extra_body = {
                        "enable_thinking": False,
                        "thinking": {"type": "disabled"},
                        "chat_template_kwargs": {"enable_thinking": False}
                    } if self.no_thinking else {},
                    stream=streaming
                )

                if streaming:
                    for chunk in response:
                        if not chunk.choices:
                            continue

                        delta = chunk.choices[0].delta
                        if hasattr(delta, "reasoning_content") and delta.reasoning_content is not None:
                            reasoning_content += delta.reasoning_content
                        if hasattr(delta, "reasoning") and delta.reasoning is not None:
                            reasoning_content = delta.reasoning
                        if hasattr(delta, "content") and delta.content:
                            content += delta.content
                
                else:
                    message = response.choices[0].message
                    assert hasattr(message, "content") and message.content is not None and message.content != ""
                    
                    if hasattr(message, "reasoning_content") and message.reasoning_content is not None:
                        reasoning_content = message.reasoning_content
                        content = message.content
                    if hasattr(message, "reasoning") and message.reasoning is not None:
                        reasoning_content = message.reasoning
                        content = message.content
                
                if reasoning_content != "":
                    _, answer_content = self.extract_reasoning(content)
                else:
                    reasoning_content, answer_content = self.extract_reasoning(content)

                # Some models put the whole response, including Action, in the
                # reasoning channel. Fall back to it when content is empty.
                if answer_content.strip() == "" and reasoning_content.strip() != "":
                    answer_content = reasoning_content

                break
            
            except Exception as e:
                print(f"Generate_response Exception {e}. Try again.")
                if hasattr(e, "message") and "Please reduce the length of the input prompt." in e.message:
                    return {"role": "assistant", "content": "Failed to generate response, please reduce the length of the input prompt."}, None
                time.sleep(min(i**2, 60))
                continue
        
        cur_time = time.strftime("%H:%M:%S", time.localtime())
        
        message = {"role": "assistant"}
        if reasoning_content != "":
            message["content"] = f"<think>{reasoning_content}</think>\n{answer_content}"
        else:
            message["content"] = answer_content
        message["time"] = cur_time
        
        return message, answer_content
