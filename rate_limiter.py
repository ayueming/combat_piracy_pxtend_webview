# rate_limiter.py
import time


class RateLimiter:
    def __init__(self, min_interval=1.0):
        self.min_interval = min_interval
        self._last_time = 0.0
        self._penalty_until = 0.0

    def wait(self):
        """在执行操作前调用，确保满足时间间隔"""
        now = time.time()

        # 1. 看看有没有因为报错产生的“惩罚等待”
        if now < self._penalty_until:
            penalty_wait = self._penalty_until - now
            time.sleep(penalty_wait)
            now = time.time()  # 刷新当前时间

        # 2. 计算距离上一次操作过了多久
        elapsed = now - self._last_time

        # 3. 如果还不够基础间隔，补足
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

        # 4. 记录本次操作时间
        self._last_time = time.time()

    def penalize(self, seconds=2.0):
        """发生异常时调用，让下一次强制多等几秒"""
        self._penalty_until = time.time() + seconds
        print(f"⏳ [RateLimiter] 触发退避，下一次请求将额外等待 {seconds} 秒")


# 实例化一个全局单例，所有业务共用（默认基础间隔 1 秒）
global_limiter = RateLimiter(min_interval=1.0)