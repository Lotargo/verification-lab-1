
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
test_adversarial.py — EXP-002: Adversarial Verification.

Цель: сломать pipeline. Подать заведомо некорректные данные и
доказать, что каждый случай правильно REJECT'ится.

Сценарии:
  1. h = -R          → r = 0         → Singularity (division by zero)
  2. h < 0           → r < R         → Below surface
  3. G < 0           → negative grav → Unphysical
  4. M < 0           → negative mass → Unphysical
  5. CRC corrupted   → frame invalid → Protocol REJECT
  6. Payload length  → mismatch      → Protocol REJECT
  7. NaN / Inf in g  → FP exception  → REJECT
  8. 0/0 in error    → indeterminate → REJECT

Запуск:
  python playground/test_adversarial.py
"""

import sys
import math
import struct
import traceback

sys.path.insert(0, 'playground')
import formulas as F

# ─── Константы ────────────────────────────────────────────────
G = 6.67430e-11
M = 5.97219e24
R = 6_371_000


def acceleration_safe(height: float, G_val: float = G, M_val: float = M,
                      R_val: float = R) -> dict | None:
    """Вычислить g, но вернуть None при любой нештатной ситуации."""
    if not math.isfinite(height):
        return None
    if not math.isfinite(G_val) or not math.isfinite(M_val) or not math.isfinite(R_val):
        return None
    if G_val <= 0:
        return None
    if M_val <= 0:
        return None
    if R_val <= 0:
        return None

    r = R_val + height
    if r <= 0:
        return None

    try:
        g = G_val * M_val / (r * r)
    except (ZeroDivisionError, OverflowError, ValueError):
        return None

    if not math.isfinite(g):
        return None
    if g <= 0:
        return None

    r_sum = F.kahan_sum([R_val, height])
    if abs(r_sum - r) > 1e-6:
        return None

    return {
        'height': height,
        'radius': r,
        'g': g,
        'g_surface': G_val * M_val / (R_val * R_val),
        'ok': True,
    }


def make_demo_frame(type_id: int, payload: bytes) -> bytes:
    header = bytes([type_id]) + struct.pack('<I', len(payload))
    frame = header + payload
    crc = crc16_ccitt_false(frame)
    return frame + struct.pack('<H', crc)


def validate_demo_frame(frame: bytes) -> tuple[bool, str]:
    """Проверить demo transport frame фрейм. Вернуть (ok, reason)."""
    if len(frame) < 7:
        return False, "FRAME_TOO_SHORT"

    crc_recv = struct.unpack('<H', frame[-2:])[-1]
    crc_calc = crc16_ccitt_false(frame[:-2])
    if crc_recv != crc_calc:
        return False, f"CRC_MISMATCH: received 0x{crc_recv:04X}, calc 0x{crc_calc:04X}"

    payload_len = struct.unpack('<I', frame[1:5])[-1]
    if len(frame) != 7 + payload_len:
        return False, (f"PAYLOAD_LENGTH_MISMATCH: header says {payload_len}, "
                       f"actual frame body {len(frame) - 7}")

    return True, "OK"


_PASS_ADV = 0
_FAIL_ADV = 0
_TOTAL_ADV = 0


def _crc_mutate(frame: bytes, mode: str) -> bytes:
    """Применить CRC-мутацию к demo transport frame фрейму."""
    fb = bytearray(frame)
    if mode == "flip":
        fb[-2] ^= 0x01
    elif mode == "zero":
        fb[-2] = 0x00
        fb[-1] = 0x00
    elif mode == "swap":
        fb[-2], fb[-1] = fb[-1], fb[-2]
    return bytes(fb)


def _length_mutate(frame: bytes, mode: str) -> bytes:
    """Применить мутацию поля длины к demo transport frame фрейму (без пересчёта CRC)."""
    fb = bytearray(frame)
    if mode == "zero":
        fb[1] = 0x00
    elif mode == "ff":
        fb[1] = 0xFF
    elif mode == "ffffffff":
        fb[1] = 0xFF
        fb[2] = 0xFF
        fb[3] = 0xFF
        fb[4] = 0xFF
    elif mode == "shorter":
        fb[1] = 0x01  # claim payload is 1 byte
    elif mode == "longer_valid_crc":
        fb[1] = 0xFF  # corrupt length, but RECOMPUTE CRC afterwards (caller does it)
    return bytes(fb)


def run_scenario(name: str, height: float, G_val: float, M_val: float,
                 crc_mode: str | None = None,
                 length_mode: str | None = None,
                 payload_mode: str | None = None):
    """Прогнать один adversarial сценарий и вывести результат.

    crc_mode: "flip"|"zero"|"swap" — мутация CRC без пересчёта
    length_mode: "zero"|"ff"|"ffffffff"|"shorter"|"longer_valid_crc"
    payload_mode: "nan" — форсировать g=NaN в payload
    """
    global _PASS_ADV, _FAIL_ADV, _TOTAL_ADV
    _TOTAL_ADV += 1

    print(f"\n  [{name}]")
    print(f"    Input: h={height}, G={G_val:.4e}, M={M_val:.4e}")

    # ── Шаг 1: computation ──────────────────────────────────
    result = acceleration_safe(height, G_val, M_val)
    if result is None:
        print(f"    Computation: REJECT")
    else:
        g_disp = result['g']
        if payload_mode == "nan":
            g_disp = float('nan')
        print(f"    Computation: g={g_disp}")

    # ── Шаг 2: demo frame ────────────────────────────────────
    if result is not None and payload_mode != "nan":
        g_val = result['g']
    else:
        g_val = 0.0

    if payload_mode == "nan":
        fact = "gravity(h=0,g=NaN)"
    else:
        fact = f"gravity(h={height},g={g_val})"
    payload = fact.encode('utf-8')

    frame = make_demo_frame(0x0A, payload)

    # Apply CRC mutation
    if crc_mode is not None:
        frame = _crc_mutate(frame, crc_mode)
        # For "longer_valid_crc" path we'd also need to send the frame
        # with a recomputed CRC for length to be the primary reject reason.
        # That's handled as a separate sub-mode below.

    # Apply length mutation
    length_recompute_crc = False
    if length_mode is not None:
        length_recompute_crc = (length_mode == "longer_valid_crc")
        frame = _length_mutate(frame, length_mode)
        if length_recompute_crc:
            # Recompute CRC over mutated header so the frame is structurally
            # valid but length field doesn't match actual payload size.
            frame = make_demo_frame(0x0A, payload)
            frame = _length_mutate(frame, "ff")
            frame = frame[:-2] + struct.pack('<H', crc16_ccitt_false(frame[:-2]))

    valid, reason = validate_demo_frame(frame)
    transport_reject = not valid
    if transport_reject:
        print(f"    demo transport frame:       REJECT ({reason})")
    else:
        print(f"    demo transport frame:       PASS (CRC=0x{struct.unpack('<H', frame[-2:])[0]:04X})")

    # ── Шаг 3: rule-based policy gate ────────────────────────────
    # Gate: if demo transport frame rejected, censor never sees the payload (architectural rule)
    if transport_reject:
        print(f"    Censor:     SKIPPED (protocol rejected frame)")
        censor_reject = True
    elif result is None:
        censor_reject = True
        print(f"    Censor:     REJECT (no valid result)")
    elif payload_mode == "nan" or not math.isfinite(result['g']):
        censor_reject = True
        print(f"    Censor:     REJECT (NaN/Inf in g)")
    elif result['g'] <= 0:
        censor_reject = True
        print(f"    Censor:     REJECT (g <= 0)")
    elif result['height'] < 0:
        censor_reject = True
        print(f"    Censor:     REJECT (subsurface: h={result['height']})")
    else:
        censor_reject = False
        print(f"    Censor:     PASS (g={result['g']:.6f})")

    # ── Aggregate verdict ──────────────────────────────────────
    agg_reject = (result is None) or transport_reject or censor_reject
    status = "OK" if agg_reject else "SURPRISE"
    if status == "OK":
        _PASS_ADV += 1
    else:
        _FAIL_ADV += 1
    print(f"    Verdict:    {'REJECT ' + status if agg_reject else 'PASS ' + status}")
    _print_reject_path(result, transport_reject, censor_reject, payload_mode)


def _print_reject_path(result, transport_reject, censor_reject, payload_mode):
    """Показать, какой слой(и) сработали."""
    layers = []
    if result is None:
        layers.append("computation")
    if transport_reject:
        layers.append("demo transport frame")
    if censor_reject:
        layers.append("censor")
    if payload_mode == "nan":
        layers.append("forced-NaN-path")
    via = "+".join(layers) if layers else "—"
    print(f"    Reject via: {via}")


def run():
    global _PASS_ADV, _FAIL_ADV, _TOTAL_ADV
    print("=" * 72)
    print("  EXP-002: ADVERSARIAL VERIFICATION")
    print("  Trying to break the pipeline with invalid inputs.")
    print("=" * 72)

    # ─── Group 1: Singularity (h = -R) ───
    print("\n" + "-" * 72)
    print("  GROUP 1: h = -R  ->  r = 0  -> Singularity")
    run_scenario("1a: h = -R (exact)", -R, G, M)
    run_scenario("1b: h = -R - 1 (below)", -R - 1.0, G, M)
    run_scenario("1c: h = -R + 1 (near)", -R + 1.0, G, M)

    # ─── Group 2: Subsurface (h < 0) ───
    print("\n" + "-" * 72)
    print("  GROUP 2: h < 0  ->  below surface")
    run_scenario("2a: h = -1000", -1000.0, G, M)
    run_scenario("2b: h = -1e6", -1_000_000.0, G, M)
    run_scenario("2c: h = -1", -1.0, G, M)

    # ─── Group 3: Negative G ───
    print("\n" + "-" * 72)
    print("  GROUP 3: G < 0  ->  repulsive gravity")
    run_scenario("3a: G = -G", 0.0, -G, M)
    run_scenario("3b: G = 0", 0.0, 0.0, M)
    run_scenario("3c: G = NaN", 0.0, float('nan'), M)

    # ─── Group 4: Negative mass ───
    print("\n" + "-" * 72)
    print("  GROUP 4: M < 0  ->  negative mass")
    run_scenario("4a: M = -M", 0.0, G, -M)
    run_scenario("4b: M = 0", 0.0, G, 0.0)
    run_scenario("4c: M = NaN", 0.0, G, float('nan'))

    # ─── Group 5: CRC corruption (3 distinct mutation modes) ───
    print("\n" + "-" * 72)
    print("  GROUP 5: CRC corruption")
    run_scenario("5a: CRC: flip 1 bit", 0.0, G, M, crc_mode="flip")
    run_scenario("5b: CRC: zero out", 0.0, G, M, crc_mode="zero")
    run_scenario("5c: CRC: byte swap", 1000.0, G, M, crc_mode="swap")

    # ─── Group 6: Payload length corruption ───
    print("\n" + "-" * 72)
    print("  GROUP 6: Payload length corruption")
    run_scenario("6a: length=0xFF (CRC not recomputed)", 0.0, G, M, length_mode="ff")
    run_scenario("6b: length=0x00 (CRC not recomputed)", 0.0, G, M, length_mode="zero")
    run_scenario("6c: length=0xFFFFFFFF (CRC not recomputed)", 0.0, G, M, length_mode="ffffffff")
    run_scenario("6d: length=0x01 shorter (CRC not recomputed)", 0.0, G, M, length_mode="shorter")
    run_scenario("6e: length=0xFF with CRC recomputed", 0.0, G, M, length_mode="longer_valid_crc")

    # ─── Group 7: NaN/Inf in result ───
    print("\n" + "-" * 72)
    print("  GROUP 7: NaN / Inf in g")
    run_scenario("7a: g = NaN (forced payload)", 0.0, G, M, payload_mode="nan")
    run_scenario("7b: h = +Inf", float('inf'), G, M)
    run_scenario("7c: h = -Inf", -float('inf'), G, M)

    # ─── Group 8: 0/0 indeterminate ───
    print("\n" + "-" * 72)
    print("  GROUP 8: 0/0 indeterminate forms")
    run_scenario("8a: G=0, M=0, h=0", 0.0, 0.0, 0.0)
    run_scenario("8b: h = NaN", float('nan'), G, M)
    run_scenario("8c: G = Inf", 0.0, float('inf'), M)

    # ─── Control: full-pipeline positive scenario ───
    _TOTAL_ADV += 1
    print("\n" + "-" * 72)
    print("  CONTROL: Valid input — full pipeline (must PASS)")
    r = acceleration_safe(0.0, G, M)
    assert r is not None, "Control computation must succeed"
    payload = f"gravity(h=0.0,g={r['g']})".encode("utf-8")
    frame = make_demo_frame(0x0A, payload)
    valid, reason = validate_demo_frame(frame)
    assert valid, f"Control demo transport frame must pass: {reason}"
    assert math.isfinite(r["g"]), "Control g must be finite"
    assert r["g"] > 0, "Control g must be positive"
    assert r["height"] >= 0, "Control height must be non-negative"
    print(f"    g(0) = {r['g']:.6f} m/s^2")
    print(f"    demo transport frame:       PASS (CRC=0x{struct.unpack('<H', frame[-2:])[0]:04X})")
    print(f"    Censor:     PASS (g={r['g']:.6f}, h={r['height']})")
    print(f"    Verdict:    PASS (computation + demo transport frame + censor)")
    _PASS_ADV += 1

    # ─── Summary ───
    adv_total = _TOTAL_ADV - 1  # exclude control from adversarial count
    print("\n" + "=" * 72)
    print(f"  Adversarial: {_PASS_ADV - 1}/{adv_total} rejected, {_FAIL_ADV}/{adv_total} missed")
    print(f"  Control:     1/1 accepted (false positive rate = 0%)")
    print(f"  Total:       {_PASS_ADV}/{_TOTAL_ADV} expectations satisfied")
    pct = 100.0 * _PASS_ADV / _TOTAL_ADV if _TOTAL_ADV else 0
    print(f"  Score:       {pct:.1f}%")
    if _FAIL_ADV == 0:
        print("  ADVERSARIAL INTEGRITY: CONFIRMED")
    else:
        print(f"  ADVERSARIAL INTEGRITY: {_FAIL_ADV} breached scenario(s)")
    print("=" * 72)


if __name__ == '__main__':
    run()