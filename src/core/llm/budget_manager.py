import asyncio
import time
import tiktoken
import logging
from collections import deque

logger = logging.getLogger(__name__)

class TokenBudgetManager:
    """
    Manages API quotas (RPM, TPM) using rolling windows and automatically 
    pauses execution to prevent rate limit errors (429).
    """
    def __init__(self, tpm_limit: int = 250000, rpm_limit: int = 15, window_seconds: int = 60):
        # Apply a 10% safety buffer to avoid hitting the exact hard limit
        self.tpm_limit = int(tpm_limit * 0.90)
        self.rpm_limit = int(rpm_limit * 0.90)
        self.window_seconds = window_seconds
        
        self.request_history = deque() # Stores tuples of (timestamp, 1)
        self.token_history = deque()   # Stores tuples of (timestamp, token_count)
        self.lock = asyncio.Lock()

        # Lazily initialized: tiktoken may fetch the encoding file on first
        # use, and we don't want that cost (or a network call) at import time.
        self._encoder = None

    @property
    def encoder(self):
        if self._encoder is None:
            # We use cl100k_base for fast approximation of tokens
            self._encoder = tiktoken.get_encoding("cl100k_base")
        return self._encoder

    def estimate_tokens(self, text: str) -> int:
        """Estimates the number of tokens in the given text."""
        if not text:
            return 0
        return len(self.encoder.encode(text))

    async def wait_for_capacity(self, estimated_tokens: int) -> float:
        """
        Blocks until there is enough capacity to send the request.
        Returns the number of seconds waited.
        """
        waited_total = 0.0
        
        # If a single request is somehow larger than the entire TPM limit, we must reject or cap it
        if estimated_tokens > self.tpm_limit:
            logger.warning(f"Request too large ({estimated_tokens} tokens) for TPM limit ({self.tpm_limit}). Capping estimation to allow it through, but it might fail.")
            estimated_tokens = self.tpm_limit
            
        while True:
            async with self.lock:
                now = time.time()
                
                # Prune old records outside the rolling window
                while self.request_history and now - self.request_history[0][0] > self.window_seconds:
                    self.request_history.popleft()
                while self.token_history and now - self.token_history[0][0] > self.window_seconds:
                    self.token_history.popleft()
                    
                current_requests = len(self.request_history)
                current_tokens = sum(t for _, t in self.token_history)
                
                # Check if we have capacity
                if current_requests < self.rpm_limit and (current_tokens + estimated_tokens) <= self.tpm_limit:
                    # Claim capacity
                    self.request_history.append((now, 1))
                    self.token_history.append((now, estimated_tokens))
                    return waited_total
                    
                # If we don't have capacity, calculate how long to wait
                wait_time = 0.0
                if current_requests >= self.rpm_limit:
                    oldest_req_time = self.request_history[0][0]
                    wait_time = max(wait_time, (oldest_req_time + self.window_seconds) - now)
                    
                if (current_tokens + estimated_tokens) > self.tpm_limit:
                    # We need to wait until enough tokens fall off the window
                    tokens_to_free = (current_tokens + estimated_tokens) - self.tpm_limit
                    freed = 0
                    for ts, toks in self.token_history:
                        freed += toks
                        if freed >= tokens_to_free:
                            wait_time = max(wait_time, (ts + self.window_seconds) - now)
                            break
                            
            if wait_time > 0:
                # Add a small 0.5s buffer to the wait time
                wait_time += 0.5
                logger.info(f"TokenBudgetManager: Quota exceeded (Tokens: {current_tokens}/{self.tpm_limit}). Waiting {wait_time:.1f}s.")
                await asyncio.sleep(wait_time)
                waited_total += wait_time
            else:
                # Fallback safeguard to prevent tight spinning
                await asyncio.sleep(1.0)
                waited_total += 1.0

# Global instance, configured from settings so quotas can be tuned per deployment
from src.config import settings

budget_manager = TokenBudgetManager(
    tpm_limit=settings.LLM_TPM_LIMIT,
    rpm_limit=settings.LLM_RPM_LIMIT
)
