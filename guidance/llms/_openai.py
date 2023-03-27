import openai
import pathlib
import diskcache
import os
import time
import requests
import warnings
import time
import collections
import tiktoken
import re
curr_dir = pathlib.Path(__file__).parent.resolve()
_file_cache = diskcache.Cache(f"{curr_dir}/_openai.diskcache")

class MalformedPromptException(Exception):
    pass
def prompt_to_messages(prompt):
    messages = []
    start_tags = re.findall(r'<\|im_start\|>', prompt)
    end_tags = re.findall(r'<\|im_end\|>', prompt)
    # if len(start_tags) != len(end_tags):
    #     raise MalformedPromptException("Malformed prompt: start and end tags are not properly paired")

    pattern = r'<\|im_start\|>(\w+)(.*?)(?=<\|im_end\|>)'
    matches = re.findall(pattern, prompt, re.DOTALL)

    if not matches:
        return [{'role': 'user', 'content': prompt.strip()}]

    for match in matches:
        role, content = match
        content = content.strip()
        messages.append({'role': role, 'content': content})

    return messages

def add_text_to_chat_completion(chat_completion):
    for c in chat_completion['choices']:
        c['text'] = c['message']['content']
        # c['text'] = f'<|im_start|>{c["message"]["role"]}\n{c["message"]["content"]}<|im_end|>'
    

    


class OpenAI():
    def __init__(self, model=None, caching=True, max_retries=5, max_calls_per_min=60, token=None, endpoint=None, temperature=0.0, chat_completion=False):

        # fill in default model value
        if model is None:
            model = os.environ.get("OPENAI_MODEL", None)
        if model is None:
            try:
                with open(os.path.expanduser('~/.openai_model'), 'r') as file:
                    model = file.read().replace('\n', '')
            except:
                pass
        
        # fill in default API key value
        if token is None: # get from environment variable
            token = os.environ.get("OPENAI_API_KEY", openai.api_key)
        if token is not None and not token.startswith("sk-") and os.path.exists(os.path.expanduser(token)): # get from file
            with open(os.path.expanduser(token), 'r') as file:
                token = file.read().replace('\n', '')
        if token is None: # get from default file location
            try:
                with open(os.path.expanduser('~/.openai_api_key'), 'r') as file:
                    token = file.read().replace('\n', '')
            except:
                pass
        
        # fill in default endpoint value
        if endpoint is None:
            endpoint = os.environ.get("OPENAI_ENDPOINT", None)

        self._encoding = tiktoken.get_encoding("cl100k_base")
        self.chat_completion = chat_completion
        
        self.model = model
        self.caching = caching
        self.max_retries = max_retries
        self.max_calls_per_min = max_calls_per_min
        self.token = token.replace("Bearer ", "")
        self.endpoint = endpoint
        self.current_time = time.time()
        self.call_history = collections.deque()
        self.temperature = temperature

        if self.endpoint is None:
            self.caller = self._library_call
        else:
            self.caller = self._rest_call
    
    def __call__(self, prompt, stop=None, temperature=None, n=1, max_tokens=1000, logprobs=None, top_p=1.0, echo=False, logit_bias=None, cache_seed=0):
        """ Generate a completion of the given prompt.
        """

        if temperature is None:
            temperature = self.temperature

        key = "_---_".join([str(v) for v in (self.model, prompt, stop, temperature, n, max_tokens, logprobs, echo, logit_bias, cache_seed)])
        if key not in _file_cache or not self.caching:

            # ensure we don't exceed the rate limit
            if self.count_calls() > self.max_calls_per_min:
                time.sleep(1)        

            fail_count = 0
            while True:
                try_again = False
                try:
                    self.add_call()
                    call_args = {
                        "model": self.model,
                        "prompt": prompt,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                        "top_p": top_p,
                        "n": n,
                        "stop": stop,
                        "logprobs": logprobs,
                        "echo": echo
                    }
                    if logit_bias is not None:
                        call_args["logit_bias"] = logit_bias
                    out = self.caller(**call_args)

                except openai.error.RateLimitError:
                    time.sleep(3)
                    try_again = True
                    fail_count += 1
                
                if not try_again:
                    break

                if fail_count > self.max_retries:
                    raise Exception(f"Too many (more than {self.max_retries}) OpenAI API RateLimitError's in a row!")

            _file_cache[key] = out
        return _file_cache[key]

    # Define a function to add a call to the deque
    def add_call(self):
        # Get the current timestamp in seconds
        now = time.time()
        # Append the timestamp to the right of the deque
        self.call_history.append(now)

    # Define a function to count the calls in the last 60 seconds
    def count_calls(self):
        # Get the current timestamp in seconds
        now = time.time()
        # Remove the timestamps that are older than 60 seconds from the left of the deque
        while self.call_history and self.call_history[0] < now - 60:
            self.call_history.popleft()
        # Return the length of the deque as the number of calls
        return len(self.call_history)

    def _library_call(self, **kwargs):
        """ Call the OpenAI API using the python package.

        Note that is uses the local auth token, and does not rely on the openai one.
        """
        prev_key = openai.api_key
        openai.api_key = self.token
        if self.chat_completion:
            kwargs['messages'] = prompt_to_messages(kwargs['prompt'])
            del kwargs['prompt']
            del kwargs['echo']
            del kwargs['logprobs']
            print(kwargs)
            out = openai.ChatCompletion.create(**kwargs)
            add_text_to_chat_completion(out)
        else:
            out = openai.Completion.create(**kwargs)
        openai.api_key = prev_key
        return out

    def _rest_call(self, **kwargs):
        """ Call the OpenAI API using the REST API.
        """

        # Define the request headers
        headers = {
            'Content-Type': 'application/json'
        }
        if self.token is not None:
            headers['Authorization'] = f"Bearer {self.token}"

        # Define the request data
        data = {
            "prompt": kwargs["prompt"],
            "max_tokens": kwargs.get("max_tokens", None),
            "temperature": kwargs.get("temperature", 0.0),
            "top_p": kwargs.get("top_p", 1.0),
            "n": kwargs.get("n", 1),
            "stream": False,
            "logprobs": kwargs.get("logprobs", None),
            'stop': kwargs.get("stop", None),
            "echo": kwargs.get("echo", False)
        }
        if self.chat_completion:
            data['messages'] = prompt_to_messages(data['prompt'])
            del data['prompt']
            del data['echo']
            del data['stream']

        # Send a POST request and get the response
        response = requests.post(self.endpoint, headers=headers, json=data)
        if response.status_code != 200:
            raise Exception("Response is not 200: " + response.text)
        response = response.json()
        if self.chat_completion:
            add_text_to_chat_completion(response)
        return response
    
    def encode(self, string):
        return self._encoding.encode(string)
    
    def decode(self, tokens):
        return self._encoding.decode(tokens)

    # def tokenize(self, strings):
    #     fail_count = 0
    #     while True:
    #         try_again = False
    #         try:
    #             out = self.caller(
    #                 model=self.model, prompt=strings, max_tokens=1, temperature=0, logprobs=0, echo=True
    #             )

    #         except openai.error.RateLimitError:
    #             time.sleep(3)
    #             try_again = True
    #             fail_count += 1
            
    #         if not try_again:
    #             break

    #         if fail_count > self.max_retries:
    #             raise Exception(f"Too many (more than {self.max_retries}) OpenAI API RateLimitError's in a row!")
        
    #     if isinstance(strings, str):
    #         return out["choices"][0]["logprobs"]["tokens"][:-1]
    #     else:
    #         return [choice["logprobs"]["tokens"][:-1] for choice in out["choices"]]



# Define a deque to store the timestamps of the calls

