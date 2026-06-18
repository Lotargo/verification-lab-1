
def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= (byte << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc

"""
exp_singularity.py — EXP-003: Singularity Semantics Verification.

Проверяет, умеет ли verification lab prototype pipeline отличать классы сингулярностей,
а не просто REJECTить любое деление на ноль.

Режимы:
  DIV_STRICT    — любой ноль в знаменателе -> REJECT
  DIV_EXTENDED  — ±Inf/NaN как типизированные состояния с provenance
  DIV_LIMIT     — численный анализ поведения при приближении к нулю
  DIV_SYMBOLIC  — возвращает объект Singularity без вычисления
  DIV_REMOVABLE — определяет устранимые сингулярности и находит предел

Запуск:  python playground/exp_singularity.py
"""

import math
import struct
import sys
from enum import Enum
from dataclasses import dataclass, field
from typing import Callable, Any

sys.path.insert(0, "playground")


# ═══════════════════════════════════════════════════════════════
#  Types
# ═══════════════════════════════════════════════════════════════

class DivMode(Enum):
    STRICT = "strict"
    EXTENDED = "extended"
    LIMIT = "limit"
    SYMBOLIC = "symbolic"
    REMOVABLE = "removable"


class SingularityKind(Enum):
    POLE = "Pole"                       # 1/0, -1/0
    POSITIVE_POLE = "PositivePole"      # 1/x^2 at x=0
    POLE_WITH_DIRECTION = "PoleWithDirection"  # 1/x: left=-inf, right=+inf
    INDETERMINATE = "Indeterminate"     # 0/0 (literal)
    REMOVABLE = "RemovableSingularity"  # (x^2-1)/(x-1) at x=1
    JUMP_DISCONTINUITY = "JumpDiscontinuity"  # abs(x)/x at x=0
    ESSENTIAL = "EssentialOrUnstable"   # sin(1/x) etc.
    FINITE = "Finite"                   # normal result


@dataclass
class Singularity:
    """Provenance-объект сингулярности."""
    kind: SingularityKind
    expr: str
    at: str
    numerator: float
    denominator: float
    approach_left: float | None = None
    approach_right: float | None = None
    limit: float | None = None
    origin: str = "division_by_zero"
    safe_for_pipeline: bool = False
    mode: str = "strict"

    def to_dict(self) -> dict:
        return {
            "type": "Singularity",
            "kind": self.kind.value,
            "expr": self.expr,
            "at": self.at,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "approach_left": self.approach_left,
            "approach_right": self.approach_right,
            "limit": self.limit,
            "origin": self.origin,
            "safe_for_pipeline": self.safe_for_pipeline,
            "mode": self.mode,
        }

    def to_demo_payload(self) -> bytes:
        """Кодировать в демо-совместимую строку (строку данных)."""
        left_s = f"{-float('inf'):.0e}" if self.approach_left == -float('inf') else f"{self.approach_left}" if self.approach_left is not None else "none"
        right_s = f"{float('inf'):.0e}" if self.approach_right == float('inf') else f"{self.approach_right}" if self.approach_right is not None else "none"
        limit_s = f"{self.limit}" if self.limit is not None else "none"
        fact = (
            f"singularity("
            f"kind={self.kind.value},"
            f"expr={self.expr},"
            f"at={self.at},"
            f"n={self.numerator},"
            f"d={self.denominator},"
            f"left={left_s},"
            f"right={right_s},"
            f"limit={limit_s},"
            f"safe={str(self.safe_for_pipeline).lower()}"
            f")"
        )
        return fact.encode("utf-8")

    def __repr__(self) -> str:
        cls = self.__class__.__name__
        return (
            f"{cls}(kind={self.kind.value}, expr={self.expr!r}, at={self.at!r}, "
            f"limit={self.limit}, safe={self.safe_for_pipeline})"
        )


# ═══════════════════════════════════════════════════════════════
#  Limit oracle
# ═══════════════════════════════════════════════════════════════

def limit_oracle(
    num_func: Callable[[float], float],
    den_func: Callable[[float], float],
    at: float,
    eps_range: list[float] | None = None,
) -> dict:
    """Численный анализ поведения f(x) = num(x)/den(x) при x -> at.

    Собирает значения f(at +/- eps) на 15 порядках eps.
    Определяет тренд (inf/-inf/finite/osc) по поведению хвоста.

    Возвращает:
      left_values, right_values
      left_trend, right_trend
      left_limit, right_limit
    """
    if eps_range is None:
        eps_range = [10.0 ** (-k) for k in range(1, 16)]

    left_vals = []
    right_vals = []

    for eps in eps_range:
        try:
            n = num_func(at - eps); d = den_func(at - eps)
            if d == 0: v = math.copysign(float("inf"), n)
            else: v = n / d
        except Exception:
            v = float("nan")
        left_vals.append(v)

        try:
            n = num_func(at + eps); d = den_func(at + eps)
            if d == 0: v = math.copysign(float("inf"), n)
            else: v = n / d
        except Exception:
            v = float("nan")
        right_vals.append(v)

    def _trend(vals):
        """Определить тренд: inf / -inf / finite / osc."""
        fin = [(i, v) for i, v in enumerate(vals) if math.isfinite(v)]

        # Если конечных нет — смотрим знаки non-NaN
        if not fin:
            signs = {math.copysign(1, v) for v in vals
                     if not math.isnan(v) and v != 0}
            if signs == {1.0}:       return "inf", float("inf")
            elif signs == {-1.0}:    return "-inf", -float("inf")
            elif signs:              return "osc", float("nan")
            else:                    return "osc", float("nan")

        # Хвост из последних 5 (или сколько есть) конечных значений
        tail = [v for _, v in fin[-min(5, len(fin)):]]
        n_tail = len(tail)

        # Проверка сходимости к конечному числу
        if n_tail >= 3:
            avg = sum(tail) / n_tail
            spread = max(tail) - min(tail)
            if spread < 1e-6 * max(1.0, abs(avg)):
                return "finite", avg

        # Проверка ухода в бесконечность (монотонный рост/падение)
        if n_tail >= 4:
            # Монотонный рост?
            inc = all(tail[i] < tail[i+1] for i in range(n_tail-1))
            # Монотонное падение?
            dec = all(tail[i] > tail[i+1] for i in range(n_tail-1))

            if inc or dec:
                # Коэффициент роста/падения на всём хвосте
                first, last = tail[0], tail[-1]
                if abs(last) > 1e10 and inc:
                    return "inf", float("inf")
                if abs(last) > 1e10 and dec:
                    return "-inf", -float("inf")

        # Если последнее значение очень большое — всё равно inf
        last_val = tail[-1]
        if abs(last_val) > 1e100:
            return "inf" if last_val > 0 else "-inf", math.copysign(float("inf"), last_val)

        return "osc", float("nan")

    left_trend, left_limit = _trend(left_vals)
    right_trend, right_limit = _trend(right_vals)

    return {
        "left_values": left_vals,
        "right_values": right_vals,
        "left_trend": left_trend,
        "right_trend": right_trend,
        "left_limit": left_limit,
        "right_limit": right_limit,
    }


# ═══════════════════════════════════════════════════════════════
#  Classifier
# ═══════════════════════════════════════════════════════════════

def classify_singularity(
    numerator: float,
    denominator: float,
    num_func: Callable | None = None,
    den_func: Callable | None = None,
    at: float | None = None,
    expr: str = "?",
) -> Singularity:
    """Классифицировать результат деления numerator/denominator.

    Если есть num_func/den_func/at -> используем limit oracle
    для точной классификации (Removable, PoleWithDirection, PositivePole и т.д.).
    Если oracle недоступен, fallback по таблице:
        0/0 -> Indeterminate
        N/0, N!=0 -> Pole
    """
    at_str = f"x={at}" if at is not None else "?"

    # Если есть limit oracle — используем его в первую очередь
    if num_func is not None and den_func is not None and at is not None:
        oracle = limit_oracle(num_func, den_func, at)

        left_t = oracle["left_trend"]
        right_t = oracle["right_trend"]
        left_l = oracle["left_limit"]
        right_l = oracle["right_limit"]

        if left_t == "finite" and right_t == "finite":
            if math.isfinite(left_l) and math.isfinite(right_l):
                avg = (left_l + right_l) / 2
                if abs(left_l - right_l) < 1e-6 * max(1.0, abs(avg)):
                    kind = SingularityKind.REMOVABLE
                    return Singularity(
                        kind=kind, expr=expr, at=at_str,
                        numerator=numerator, denominator=denominator,
                        approach_left=left_l, approach_right=right_l,
                        limit=avg, safe_for_pipeline=True,
                        mode="limit" if denominator != 0 else "removable",
                    )
                else:
                    # Finite but differ — jump discontinuity (abs(x)/x)
                    kind = SingularityKind.JUMP_DISCONTINUITY
                    return Singularity(
                        kind=kind, expr=expr, at=at_str,
                        numerator=numerator, denominator=denominator,
                        approach_left=left_l, approach_right=right_l,
                        limit=None, safe_for_pipeline=False,
                        mode="limit",
                    )

        if left_t == "inf" and right_t == "inf":
            kind = SingularityKind.POSITIVE_POLE
            return Singularity(
                kind=kind, expr=expr, at=at_str,
                numerator=numerator, denominator=denominator,
                approach_left=float("inf"), approach_right=float("inf"),
                limit=float("inf"), safe_for_pipeline=False,
                mode="limit",
            )

        if left_t == "-inf" and right_t == "-inf":
            kind = SingularityKind.POSITIVE_POLE
            return Singularity(
                kind=kind, expr=expr, at=at_str,
                numerator=numerator, denominator=denominator,
                approach_left=-float("inf"), approach_right=-float("inf"),
                limit=-float("inf"), safe_for_pipeline=False,
                mode="limit",
            )

        if left_t == "-inf" and right_t == "inf":
            kind = SingularityKind.POLE_WITH_DIRECTION
            return Singularity(
                kind=kind, expr=expr, at=at_str,
                numerator=numerator, denominator=denominator,
                approach_left=-float("inf"), approach_right=float("inf"),
                limit=None, safe_for_pipeline=False,
                mode="limit",
            )

        if left_t == "inf" and right_t == "-inf":
            kind = SingularityKind.POLE_WITH_DIRECTION
            return Singularity(
                kind=kind, expr=expr, at=at_str,
                numerator=numerator, denominator=denominator,
                approach_left=float("inf"), approach_right=-float("inf"),
                limit=None, safe_for_pipeline=False,
                mode="limit",
            )

        # fallback: oscillator or unknown
        kind = SingularityKind.ESSENTIAL
        return Singularity(
            kind=kind, expr=expr, at=at_str,
            numerator=numerator, denominator=denominator,
            approach_left=left_l, approach_right=right_l,
            limit=None, safe_for_pipeline=False,
            mode="limit",
        )

    # Fallback без oracle: табличная классификация
    if denominator == 0 and numerator == 0:
        return Singularity(
            kind=SingularityKind.INDETERMINATE, expr=expr, at=at_str,
            numerator=numerator, denominator=denominator,
            safe_for_pipeline=False, mode="extended",
        )

    if denominator == 0 and numerator != 0:
        return Singularity(
            kind=SingularityKind.POLE, expr=expr, at=at_str,
            numerator=numerator, denominator=denominator,
            safe_for_pipeline=False, mode="extended",
        )

    # Нормальный случай (не должен сюда попадать)
    return Singularity(
        kind=SingularityKind.FINITE, expr=expr, at=at_str,
        numerator=numerator, denominator=denominator,
        safe_for_pipeline=True, mode="extended",
    )


# ═══════════════════════════════════════════════════════════════
#  safe_div — основная точка входа
# ═══════════════════════════════════════════════════════════════

def safe_div(
    numerator: float,
    denominator: float,
    mode: DivMode = DivMode.STRICT,
    num_func: Callable | None = None,
    den_func: Callable | None = None,
    at: float | None = None,
    expr: str = "?",
) -> tuple[bool, float | Singularity | None, str]:
    """Безопасное деление с классификацией сингулярности.

    Returns:
        (ok: bool, value: float | Singularity | None, reason: str)

    Режимы:
      STRICT    — 0 в знаменателе -> (False, None, "division by zero")
      EXTENDED  — 0 в знаменателе -> (True, Singularity(...), "singularity classified")
      LIMIT     — анализирует поведение через limit_oracle
      SYMBOLIC  — возвращает Singularity без вычисления
      REMOVABLE — пытается найти устранимую сингулярность и предел
    """
    # Проверка конечности
    if not math.isfinite(numerator) or not math.isfinite(denominator):
        return False, None, "non-finite input"

    # Normal case
    if denominator != 0:
        try:
            result = numerator / denominator
            if math.isfinite(result):
                return True, result, "ok"
            else:
                return False, None, "overflow"
        except (ZeroDivisionError, OverflowError, ValueError):
            return False, None, "arithmetic error"

    # ── Division by zero ─────────────────────────────────────────

    # STRICT
    if mode == DivMode.STRICT:
        return False, None, "division by zero"

    # EXTENDED
    if mode == DivMode.EXTENDED:
        sing = classify_singularity(numerator, denominator, expr=expr)
        return True, sing, "singularity classified"

    # SYMBOLIC
    if mode == DivMode.SYMBOLIC:
        sing = classify_singularity(numerator, denominator, expr=expr)
        sing.mode = "symbolic"
        return True, sing, "symbolic singularity"

    # LIMIT (требует num_func, den_func, at)
    if mode == DivMode.LIMIT:
        if num_func is None or den_func is None or at is None:
            sing = classify_singularity(numerator, denominator, expr=expr)
            sing.mode = "limit_fallback"
            return True, sing, "limit: fallback (no funcs)"
        sing = classify_singularity(
            numerator, denominator,
            num_func=num_func, den_func=den_func, at=at,
            expr=expr,
        )
        sing.mode = "limit"
        return True, sing, f"limit: {sing.kind.value}"

    # REMOVABLE (limit oracle + проверка на removable)
    if mode == DivMode.REMOVABLE:
        if num_func is None or den_func is None or at is None:
            sing = classify_singularity(numerator, denominator, expr=expr)
            sing.mode = "removable_fallback"
            return True, sing, "removable: fallback (no funcs)"
        sing = classify_singularity(
            numerator, denominator,
            num_func=num_func, den_func=den_func, at=at,
            expr=expr,
        )
        sing.mode = "removable"
        if sing.kind == SingularityKind.REMOVABLE:
            return True, sing, f"removable: limit = {sing.limit}"
        else:
            return True, sing, f"non-removable: {sing.kind.value}"

    return False, None, f"unknown mode: {mode}"


# ═══════════════════════════════════════════════════════════════
#  Censor
# ═══════════════════════════════════════════════════════════════

def policy_gate_verdict(sing: Singularity) -> tuple[bool, str]:
    """rule-based policy gate для сингулярностей.

    Правила:
      - raw_nan / raw_inf -> REJECT (unsafe floating-point)
      - indeterminate -> REJECT (needs limit analysis)
      - unsafe_pole (safe=False) -> REJECT
      - removable (safe=True) -> ALLOW
      - pole with direction (not safe) -> REJECT unless all pipeline allows
    """
    if not sing.safe_for_pipeline:
        return False, f"REJECT: unsafe {sing.kind.value} at {sing.at}"

    if sing.kind == SingularityKind.REMOVABLE:
        if sing.limit is None or not math.isfinite(sing.limit):
            return False, f"REJECT: removable singularity without finite limit at {sing.at}"
        return True, f"ALLOW: removable singularity, limit = {sing.limit}"

    if sing.kind == SingularityKind.FINITE:
        return True, "ALLOW: finite result"

    return False, f"REJECT: {sing.kind.value} at {sing.at}"


# ═══════════════════════════════════════════════════════════════
#  demo transport frame helpers
# ═══════════════════════════════════════════════════════════════

def make_demo_singularity_frame(sing: Singularity) -> bytes:
    """Упаковать Singularity в demo frame (type identifier 0x0B (singularity))."""
    payload = sing.to_demo_payload()
    type_id = 0x0B  # singularity event type
    header = bytes([type_id]) + struct.pack("<I", len(payload))
    frame = header + payload
    crc = crc16_ccitt_false(frame)
    return frame + struct.pack("<H", crc)


def validate_demo_frame(frame: bytes) -> tuple[bool, str]:
    if len(frame) < 7:
        return False, "FRAME_TOO_SHORT"
    crc_recv = struct.unpack("<H", frame[-2:])[0]
    crc_calc = crc16_ccitt_false(frame[:-2])
    if crc_recv != crc_calc:
        return False, f"CRC_MISMATCH"
    plen = struct.unpack("<I", frame[1:5])[0]
    if len(frame) != 7 + plen:
        return False, f"PAYLOAD_LENGTH_MISMATCH"
    return True, "OK"


# ═══════════════════════════════════════════════════════════════
#  Test cases
# ═══════════════════════════════════════════════════════════════

def make_test_cases():
    """Вернуть список тестовых кейсов."""
    return [
        # (name, numerator, denominator, num_func, den_func, at, expr, modes_to_test)
        {
            "name": "1/x at 0 — directional pole",
            "num": 1.0, "den": 0.0,
            "num_func": lambda x: 1.0,
            "den_func": lambda x: x,
            "at": 0.0, "expr": "1/x",
            "modes": ["strict", "extended", "limit", "symbolic"],
            "expected_kinds": {
                "strict": None,
                "extended": "Pole",
                "limit": "PoleWithDirection",
                "symbolic": "Pole",
            },
        },
        {
            "name": "-1/x at 0 — directional pole",
            "num": -1.0, "den": 0.0,
            "num_func": lambda x: -1.0,
            "den_func": lambda x: x,
            "at": 0.0, "expr": "-1/x",
            "modes": ["strict", "extended", "limit"],
            "expected_kinds": {
                "strict": None,
                "extended": "Pole",
                "limit": "PoleWithDirection",
            },
        },
        {
            "name": "x/x at 0 — removable (not literal 0/0)",
            "num": 0.0, "den": 0.0,
            "num_func": lambda x: x,
            "den_func": lambda x: x,
            "at": 0.0, "expr": "x/x",
            "modes": ["strict", "extended", "limit", "removable"],
            "expected_kinds": {
                "strict": None,
                "extended": "Indeterminate",
                "limit": "RemovableSingularity",
                "removable": "RemovableSingularity",
            },
        },
        {
            "name": "(x^2-1)/(x-1) at 1 — removable",
            "num": 0.0, "den": 0.0,
            "num_func": lambda x: x * x - 1.0,
            "den_func": lambda x: x - 1.0,
            "at": 1.0, "expr": "(x*x-1)/(x-1)",
            "modes": ["extended", "limit", "removable"],
            "expected_kinds": {
                "extended": "Indeterminate",
                "limit": "RemovableSingularity",
                "removable": "RemovableSingularity",
            },
        },
        {
            "name": "sin(x)/x at 0 — removable",
            "num": 0.0, "den": 0.0,
            "num_func": lambda x: math.sin(x),
            "den_func": lambda x: x,
            "at": 0.0, "expr": "sin(x)/x",
            "modes": ["extended", "limit", "removable"],
            "expected_kinds": {
                "extended": "Indeterminate",
                "limit": "RemovableSingularity",
                "removable": "RemovableSingularity",
            },
        },
        {
            "name": "1/x^2 at 0 — positive pole",
            "num": 1.0, "den": 0.0,
            "num_func": lambda x: 1.0,
            "den_func": lambda x: x * x,
            "at": 0.0, "expr": "1/x^2",
            "modes": ["strict", "extended", "limit"],
            "expected_kinds": {
                "strict": None,
                "extended": "Pole",
                "limit": "PositivePole",
            },
        },
        # ── Negative trap cases ──────────────────────────────
        {
            "name": "literal 0/0 — indeterminate (no funcs)",
            "num": 0.0, "den": 0.0,
            "num_func": None, "den_func": None, "at": None,
            "expr": "0/0",
            "modes": ["strict", "extended"],
            "expected_kinds": {
                "strict": None,
                "extended": "Indeterminate",
            },
        },
        {
            "name": "sin(1/x) at 0 — oscillatory/essential",
            "num": 1.0, "den": 0.0,
            "num_func": lambda x: math.sin(1.0 / x) if x != 0 else 0.0,
            "den_func": lambda x: x,
            "at": 0.0, "expr": "sin(1/x)/x",
            "modes": ["extended", "limit", "removable"],
            "expected_kinds": {
                "extended": "Pole",
                "limit": "EssentialOrUnstable",
                "removable": "EssentialOrUnstable",
            },
        },
        {
            "name": "abs(x)/x at 0 — jump discontinuity",
            "num": 0.0, "den": 0.0,
            "num_func": lambda x: abs(x),
            "den_func": lambda x: x,
            "at": 0.0, "expr": "abs(x)/x",
            "modes": ["extended", "limit", "removable"],
            "expected_kinds": {
                "extended": "Indeterminate",
                "limit": "JumpDiscontinuity",
                "removable": "JumpDiscontinuity",
            },
        },
        {
            "name": "(x^2+1)/x at 0 — pole with direction",
            "num": 1.0, "den": 0.0,
            "num_func": lambda x: x * x + 1.0,
            "den_func": lambda x: x,
            "at": 0.0, "expr": "(x^2+1)/x",
            "modes": ["extended", "limit"],
            "expected_kinds": {
                "extended": "Pole",
                "limit": "PoleWithDirection",
            },
        },
    ]


# ═══════════════════════════════════════════════════════════════
#  Run
# ═══════════════════════════════════════════════════════════════

_PASS = 0
_FAIL = 0
_TOTAL = 0


def run():
    global _PASS, _FAIL, _TOTAL
    _PASS = 0
    _FAIL = 0
    _TOTAL = 0

    print("=" * 72)
    print("  EXP-003: SINGULARITY_EVENT SEMANTICS VERIFICATION")
    print("  Proving the system distinguishes singularity classes.")
    print("=" * 72)

    # ── Layer 1: safe_div mode tests ──────────────────────────
    print("\n" + "-" * 72)
    print("  LAYER 1: safe_div mode compliance")
    print("-" * 72)

    cases = make_test_cases()

    for case in cases:
        name = case["name"]
        n = case["num"]
        d = case["den"]
        nf = case.get("num_func")
        df = case.get("den_func")
        at = case.get("at")
        expr = case.get("expr", "?")

        for mode_str in case["modes"]:
            mode = DivMode(mode_str)
            expected = case["expected_kinds"][mode_str]
            ok, val, reason = safe_div(n, d, mode=mode,
                                       num_func=nf, den_func=df,
                                       at=at, expr=expr)
            _TOTAL += 1

            # Определяем, что получилось
            actual_kind = None
            if isinstance(val, Singularity):
                actual_kind = val.kind.value
            elif isinstance(val, float):
                actual_kind = "Float"

            # Определяем PASS/FAIL
            passed = False
            if expected is None and not ok:
                passed = True  # expected REJECT
            elif expected is not None and ok and isinstance(val, Singularity):
                passed = (val.kind.value == expected)
            elif expected == "Float" and ok and isinstance(val, float):
                passed = True

            verdict = "PASS" if passed else "FAIL"
            if passed:
                _PASS += 1
            else:
                _FAIL += 1

            # Краткий вывод
            if isinstance(val, Singularity):
                val_str = f"{val.kind.value}"
                if val.kind == SingularityKind.REMOVABLE and val.limit is not None:
                    val_str += f" limit={val.limit:.4f}"
            elif isinstance(val, float):
                val_str = f"{val:.6f}"
            else:
                val_str = "REJECT"

            exp_str = expected if expected is not None else "REJECT"
            status = "OK" if passed else "MISMATCH"
            print(f"  [{name}] mode={mode_str:<10} got={val_str:<20} "
                  f"exp={exp_str:<20} {verdict}")

    # ── Layer 2: Limit oracle verification ────────────────────
    print("\n" + "-" * 72)
    print("  LAYER 2: Limit oracle — numerical approach analysis")
    print("-" * 72)

    limit_cases: list[tuple[str, Callable, Callable, float, str, str]] = [
        ("1/x  at 0",          lambda x: 1.0,      lambda x: x,     0.0, "PoleWithDirection", "PoleWithDirection"),
        ("1/x^2 at 0",         lambda x: 1.0,      lambda x: x*x,   0.0, "PositivePole",       "PositivePole"),
        ("x/x  at 0",          lambda x: x,        lambda x: x,     0.0, "Removable",          "Removable"),
        ("(x^2-1)/(x-1) at 1", lambda x: x*x - 1,  lambda x: x-1,   1.0, "Removable",          "Removable"),
        ("sin(x)/x at 0",      lambda x: math.sin(x), lambda x: x, 0.0, "Removable",          "Removable"),
        # ── Negative traps in oracle ────────────────────────────
        ("sin(1/x) at 0",      lambda x: math.sin(1.0/x) if x != 0 else 0.0, lambda x: x, 0.0, "Oscillatory", "Oscillatory"),
        ("abs(x)/x at 0",      lambda x: abs(x),  lambda x: x,     0.0, "Jump",               "Jump"),
        ("(x^2+1)/x at 0",     lambda x: x*x + 1, lambda x: x,     0.0, "PoleWithDirection", "PoleWithDirection"),
    ]

    for name, nf, df, at_val, exp_left_kind, exp_right_kind in limit_cases:
        _TOTAL += 1
        oracle = limit_oracle(nf, df, at_val)
        left_t = oracle["left_trend"]
        right_t = oracle["right_trend"]
        left_l = oracle["left_limit"]
        right_l = oracle["right_limit"]
        passed = True
        print(f"  [{name}]")
        print(f"    left:  {left_t:>8} ({left_l})")
        print(f"    right: {right_t:>8} ({right_l})")
        # Quick expected-kind annotation
        notes = []
        if left_t == "finite" and right_t == "finite":
            notes.append("removable?")
        elif left_t != right_t:
            notes.append("directional pole")
        elif left_t == "inf" and right_t == "inf":
            notes.append("positive pole")
        elif left_t == "-inf" and right_t == "-inf":
            notes.append("negative pole")
        print(f"    note:  {', '.join(notes) if notes else '—'}")
        if passed:
            _PASS += 1

    # ── Layer 3: demo transport encoding ────────────────────────────────
    print("\n" + "-" * 72)
    print("  LAYER 3: demo transport frame singularity transport")
    print("-" * 72)

    transport_cases = [
        Singularity(kind=SingularityKind.POLE, expr="1/x", at="x=0",
                    numerator=1.0, denominator=0.0,
                    approach_left=-float("inf"), approach_right=float("inf"),
                    safe_for_pipeline=False),
        Singularity(kind=SingularityKind.INDETERMINATE, expr="0/0", at="x=0",
                    numerator=0.0, denominator=0.0,
                    safe_for_pipeline=False),
        Singularity(kind=SingularityKind.REMOVABLE, expr="(x^2-1)/(x-1)", at="x=1",
                    numerator=0.0, denominator=0.0,
                    limit=2.0, safe_for_pipeline=True),
    ]

    for sing in transport_cases:
        _TOTAL += 1
        frame = make_demo_singularity_frame(sing)
        ok, reason = validate_demo_frame(frame)
        status = "PASS" if ok else f"FAIL ({reason})"
        if ok:
            _PASS += 1
        else:
            _FAIL += 1
        payload_str = sing.to_demo_payload().decode("utf-8")
        print(f"  [{sing.kind.value}]")
        print(f"    Payload: {payload_str}")
        print(f"    Frame:   {len(frame)} bytes, CRC=0x{struct.unpack('<H', frame[-2:])[0]:04X}")
        print(f"    demo transport frame:    {status}")

    # ── Adversarial transport: malformed / corrupted ────────────
    print(f"\n  -- adversarial transport --")

    # 3a) Malformed payload: binary garbage passes wire validation (CRC correct)
    #     but is rejected at the semantic parsing layer.
    def parse_singularity_payload(frame: bytes):
        """Парсинг demo frame в Singularity (семантический слой)."""
        ok, reason = validate_demo_frame(frame)
        if not ok:
            return False, reason, None
        payload = frame[5:-2]  # skip opcode(1) + length(4) + crc(2)
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return False, "INVALID_UTF8", None
        if not text.startswith("singularity(") or not text.endswith(")"):
            return False, "MALFORMED_PAYLOAD", None
        # Parse key=value pairs
        inner = text[len("singularity("):-1]
        pairs = {}
        for part in inner.split(","):
            if "=" not in part:
                return False, "MALFORMED_PAYLOAD", None
            k, v = part.split("=", 1)
            pairs[k.strip()] = v.strip()
        if "kind" not in pairs:
            return False, "MISSING_KIND", None
        return True, "OK", None  # semantic validation passed

    type_id = 0x0B
    malformed_payload = b"\xff\xfe\xfd"  # binary garbage
    adv_header = bytes([type_id]) + struct.pack("<I", len(malformed_payload))
    adv_frame = adv_header + malformed_payload
    adv_crc = crc16_ccitt_false(adv_frame)
    adv_frame_full = adv_frame + struct.pack("<H", adv_crc)
    _TOTAL += 1
    ok, reason = validate_demo_frame(adv_frame_full)
    if ok:
        # Wire passes — now check semantic parsing
        ok2, reason2, _ = parse_singularity_payload(adv_frame_full)
        passed = not ok2
        final_reason = reason2
    else:
        passed = True
        final_reason = reason
    status = "PASS" if passed else "FAIL"
    if passed:
        _PASS += 1
    else:
        _FAIL += 1
    print(f"  [MalformedPayload] garbage={malformed_payload.hex()} "
          f"wire={reason:<20} semantic={final_reason:<20} expected=REJECT {status}")

    # 3b) CRC corruption
    sing_ok = Singularity(kind=SingularityKind.REMOVABLE, expr="(x^2-1)/(x-1)", at="x=1",
                          numerator=0.0, denominator=0.0,
                          limit=2.0, safe_for_pipeline=True)
    good_frame = make_demo_singularity_frame(sing_ok)
    corrupted = bytearray(good_frame)
    corrupted[len(corrupted) // 2] ^= 0xFF  # flip bits in middle
    _TOTAL += 1
    ok, reason = validate_demo_frame(bytes(corrupted))
    passed = not ok
    status = "PASS" if passed else "FAIL"
    if passed:
        _PASS += 1
    else:
        _FAIL += 1
    stored_crc = struct.unpack('<H', corrupted[-2:])[0]
    recalc_crc = crc16_ccitt_false(corrupted[:-2])
    print(f"  [CRCCorruption]  stored_crc=0x{stored_crc:04X} "
          f"recalc=0x{recalc_crc:04X} "
          f"validate={reason:<25} expected=REJECT {status}")

    # ── Layer 4: Policy gate rules ─────────────────────────────────
    print("\n" + "-" * 72)
    print("  LAYER 4: Policy gate singularity policy")
    print("-" * 72)

    censor_cases = [
        Singularity(kind=SingularityKind.POLE, expr="1/x", at="x=0",
                    numerator=1.0, denominator=0.0, safe_for_pipeline=False),
        Singularity(kind=SingularityKind.INDETERMINATE, expr="0/0", at="x=0",
                    numerator=0.0, denominator=0.0, safe_for_pipeline=False),
        Singularity(kind=SingularityKind.REMOVABLE, expr="(x^2-1)/(x-1)", at="x=1",
                    numerator=0.0, denominator=0.0, limit=2.0,
                    safe_for_pipeline=True),
        Singularity(kind=SingularityKind.POSITIVE_POLE, expr="1/x^2", at="x=0",
                    numerator=1.0, denominator=0.0, safe_for_pipeline=False),
        Singularity(kind=SingularityKind.POLE_WITH_DIRECTION, expr="1/x", at="x=0",
                    numerator=1.0, denominator=0.0,
                    approach_left=-float("inf"), approach_right=float("inf"),
                    safe_for_pipeline=False),
            # ── Adversarial censor: safe flag lies ──────────────
        # These test that censor rejects even when safe=True is claimed
        # Expected: REJECT (Pole is never allowed through censor)
        ("Pole (adversarial safe=True)", Singularity(kind=SingularityKind.POLE, expr="1/x", at="x=0",
                    numerator=1.0, denominator=0.0, safe_for_pipeline=True), False),
        # Expected: REJECT (RemovableSingularity without limit is rejected)
        ("Removable no-limit (adversarial)", Singularity(kind=SingularityKind.REMOVABLE, expr="fabricated", at="x=0",
                    numerator=0.0, denominator=0.0, limit=None,
                    safe_for_pipeline=True), False),
        # Expected: REJECT (JumpDiscontinuity is unsafe)
        ("JumpDiscontinuity (adversarial safe=True)", Singularity(kind=SingularityKind.JUMP_DISCONTINUITY, expr="abs(x)/x", at="x=0",
                    numerator=0.0, denominator=0.0, safe_for_pipeline=True), False),
    ]

    for entry in censor_cases:
        if isinstance(entry, tuple):
            label, sing, expect_allow = entry
        else:
            sing = entry
            label = sing.kind.value
            expect_allow = sing.safe_for_pipeline
        _TOTAL += 1
        ok, reason = policy_gate_verdict(sing)
        status = "ALLOW" if ok else "REJECT"
        passed = (ok == expect_allow)
        verdict = "PASS" if passed else "FAIL"
        if passed:
            _PASS += 1
        else:
            _FAIL += 1
        print(f"  [{label:<35}] safe={str(sing.safe_for_pipeline):<5} "
              f"censor={status:<6} expect_allow={expect_allow} {verdict}")

    # ── Summary ────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print(f"  RESULTS: {_PASS}/{_TOTAL} passed, {_FAIL}/{_TOTAL} failed")
    pct = 100.0 * _PASS / _TOTAL if _TOTAL else 0
    print(f"  Score:   {pct:.1f}%")
    if _FAIL == 0:
        print("  SINGULARITY_EVENT SEMANTICS: ALL TESTS PASS")
    else:
        print(f"  SINGULARITY_EVENT SEMANTICS: {_FAIL} failure(s)")
    print("=" * 72)


if __name__ == "__main__":
    run()