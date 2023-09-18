import re
import functools

import guidance.endpoints
from ._lm import LM, ChatLM


class Transformers(LM):
    def __init__(self, model, caching=True, **endpoint_kwargs):
        super().__init__(model, caching=caching)
        self.model = model

        self.endpoint = guidance.endpoints.Transformers(model, **endpoint_kwargs)
        self._endpoint_session = self.endpoint.session()

class ChatTransformers(Transformers, ChatLM):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)