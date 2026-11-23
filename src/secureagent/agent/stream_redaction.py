"""Hide exact temporary test sources without leaking chunk boundaries."""

from collections import deque


class StreamRedactor:
    def __init__(self, patterns: list[str]):
        self.patterns = list(dict.fromkeys(pattern for pattern in patterns if pattern))
        self.tables = [self._prefix_table(pattern) for pattern in self.patterns]
        self.states = [0] * len(self.patterns)
        self.pending: deque[str] = deque()

    @staticmethod
    def _prefix_table(pattern: str) -> list[int]:
        table = [0] * len(pattern)
        length = 0
        for index in range(1, len(pattern)):
            while length and pattern[index] != pattern[length]:
                length = table[length - 1]
            if pattern[index] == pattern[length]:
                length += 1
            table[index] = length
        return table

    def push(self, text: str) -> str:
        if not self.patterns:
            return text
        output: list[str] = []
        for character in text:
            self.pending.append(character)
            matched = 0
            for index, pattern in enumerate(self.patterns):
                state = self.states[index]
                while state and character != pattern[state]:
                    state = self.tables[index][state - 1]
                if character == pattern[state]:
                    state += 1
                if state == len(pattern):
                    matched = max(matched, len(pattern))
                    state = self.tables[index][state - 1]
                self.states[index] = state
            if matched:
                output.extend(self.pending.popleft() for _ in range(len(self.pending) - matched))
                output.append("[临时测试代码已隐藏]")
                self.pending.clear()
                self.states = [0] * len(self.patterns)
            else:
                keep = max(self.states, default=0)
                safe = len(self.pending) - keep
                if safe:
                    output.extend(self.pending.popleft() for _ in range(safe))
        return "".join(output)

    def finish(self) -> str:
        remaining = "".join(self.pending)
        self.pending.clear()
        self.states = [0] * len(self.patterns)
        return remaining
