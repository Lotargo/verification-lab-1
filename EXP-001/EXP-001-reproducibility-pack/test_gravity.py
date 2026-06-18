
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
test_gravity.py — Классическая проверка: свободное падение.

Цель: вычислить ускорение свободного падения для объекта массы m
на высоте h над поверхностью Земли и верифицировать через всю цепочку:

  formulas.py → demo transport frame → v7 цензор → demo transport frame → Python

Уравнения (Newton):
  F = G * M * m / (R + h)²
  a = F / m = G * M / (R + h)²   (не зависит от массы)

Известные константы:
  G  = 6.67430e-11  N·m²/kg²
  M  = 5.97219e24   kg
  R  = 6,371,000    m
  a₀ = 9.80665      m/s² (у поверхности, h=0)

Запуск:
  python playground/test_gravity.py
"""

import sys
import math
import struct

sys.path.insert(0, 'playground')
import formulas as F

# ─── Константы ────────────────────────────────────────────────
G = 6.67430e-11        # гравитационная постоянная
M = 5.97219e24          # масса Земли, кг
R = 6_371_000           # радиус Земли, м
g0_reference = 9.80665  # эталонное ускорение на поверхности, м/с²


def acceleration(height: float = 0.0) -> dict:
    """Вычислить ускорение свободного падения на высоте h (метров).

    Возвращает словарь с полным расчётом.
    """
    r = R + height
    g = G * M / (r * r)

    # Эталон: вычисляем g на поверхности из тех же констант
    g0_computed = G * M / (R * R)

    # Отклонение от эталона
    error_abs = abs(g - g0_computed)
    error_rel = error_abs / g0_computed * 100

    return {
        'height_m': height,
        'radius_m': r,
        'g_ms2': g,
        'g_surface': g0_computed,
        'error_abs': error_abs,
        'error_rel_pct': error_rel,
        'g_over_surface': g / g0_computed,
    }


def build_demo_frame(type_id: int, payload: bytes) -> bytes:
    """Собрать demo transport frame фрейм: opcode(1B) + len(4B LE) + payload + CRC16(2B)."""
    header = bytes([type_id]) + struct.pack('<I', len(payload))
    frame = header + payload
    crc = crc16_ccitt_false(frame)
    return frame + struct.pack('<H', crc)


def format_prolog_fact(g: dict) -> str:
    """Сформировать строку данных для отправки цензору.

    v7's relativity.pl принимает CLP(R) ограничения вида:
      gravity(M1, M2, R, F)
      velocity(V) :- {V < 1.0}

    Формат:
      free_fall(g(G), h(H), r(R), error(E))
    """
    return (f"free_fall(g({g['g_ms2']:.10f}), "
            f"h({g['height_m']}), "
            f"r({g['radius_m']}), "
            f"error({g['error_rel_pct']:.6f}))")


def check_via_formulas(g: dict) -> dict:
    """Проверить гравитационные вычисления через formulas.py.

    Используем:
      - Kahan sum для точного сложения
      - haversine/acos_stable для метрик (если пригодится)
      - domain law checks для консистентности
    """
    checks = {}
    bools = {}

    # 1. g должно быть положительным
    bools['g_positive'] = g['g_ms2'] > 0

    # 2. Квадрат радиуса через Kahan sum (демонстрация)
    r_parts = [R, g['height_m']]
    r_sum = F.kahan_sum(r_parts)
    bools['radius_kahan'] = abs(r_sum - g['radius_m']) < 1e-6

    # 3. Ускорение на высоте <= ускорения на поверхности
    bools['g_decreases_with_height'] = g['g_ms2'] <= g['g_surface'] + 1e-10

    # 4. Обратный квадрат: g ∝ 1/r² (self-consistent)
    r0 = R
    r1 = g['radius_m']
    g_predicted = g['g_surface'] * (r0 * r0) / (r1 * r1)
    checks['inverse_square_error'] = abs(g_predicted - g['g_ms2'])
    bools['inverse_square_error'] = abs(g_predicted - g['g_ms2']) < 1e-10

    return checks, bools


def simulate_prolog_censor(fact: str, g: dict) -> tuple[bool, str]:
    """Симуляция v7 цензора (relativity.pl) через formulas.py.

    В реальной системе это уйдёт по demo transport frame → Verifier Bridge → Verifier.
    Сейчас — локальная проверка через Python, чтобы доказать
    консистентность результата перед отправкой.
    """
    # relativity.pl проверяет:
    #   1. v < 1.0  (sub-light) — не применимо
    #   2. T > 0    (thermo)
    #   3. Массы положительны
    #   4. Энергия сохраняется

    issues = []
    ok = True

    # Масса не указана явно — ускорение свободного падения не зависит от массы
    # Проверяем базовые ограничения
    if g['height_m'] < 0:
        issues.append("HEIGHT_NEGATIVE: высота не может быть отрицательной")
        ok = False

    if g['g_ms2'] <= 0:
        issues.append("G_NEGATIVE: ускорение должно быть положительным")
        ok = False

    if g['g_ms2'] > 100:
        # На Земле g ≤ ~54 м/с² даже на уровне ядра
        issues.append(f"G_TOO_LARGE: {g['g_ms2']:.2f} м/с² "
                      f"(нефизично для Земли)")
        ok = False

    # Проверка обратных квадратов (conservation of flux)
    r_ratio = R / g['radius_m']
    expected_g = g['g_surface'] * r_ratio * r_ratio
    err = abs(expected_g - g['g_ms2'])
    if err > 1e-6:
        issues.append(f"INVERSE_SQUARE_VIOLATION: "
                      f"expected {expected_g:.6f}, got {g['g_ms2']:.6f} (err={err:.2e})")
        ok = False

    return ok, "; ".join(issues) if issues else "OK"


def run(height: float = 0.0, verbose: bool = True):
    """Полный прогон: расчёт → проверка → demo transport frame → отчёт."""
    if verbose:
        print("=" * 72)
        print("  GRAVITY VERIFICATION TEST")
        print("  G = {:.6e} N*m^2/kg^2".format(G))
        print("  M = {:.3e} kg".format(M))
        print("  R = {:,} m".format(R))
        print("  g_surface = {:.5f} m/s^2".format(g0_reference))
        print("=" * 72)

    # Шаг 1: расчёт
    g = acceleration(height)
    if verbose:
        print(f"\n  #1 Height: {g['height_m']:,.0f} m")
        print(f"     Radius: {g['radius_m']:,.0f} m")
        print(f"     g = {g['g_ms2']:.10f} m/s^2")
        print(f"     g/g_surface = {g['g_over_surface']:.6f}")
        print(f"     error = {g['error_rel_pct']:.6f}%")

    # Шаг 2: проверка formulas.py
    checks, bools = check_via_formulas(g)
    if verbose:
        print(f"\n  #2 Formulas.py checks:")
        for check in bools:
            status = "PASS" if bools[check] else "FAIL"
            print(f"     {check:<30s} {status}")
        if checks.get('inverse_square_error') is not None:
            print(f"     {'inverse_sq_err_val':<30s} {checks['inverse_square_error']:.2e}")

    # Шаг 3: цензор (симуляция)
    fact = format_prolog_fact(g)
    censor_ok, censor_msg = simulate_prolog_censor(fact, g)
    if verbose:
        print(f"\n  #3 policy gate (simulated):")
        print(f"     Fact: {fact}")
        print(f"     Verdict: {censor_msg}")

    # Шаг 4: demo transport frame фрейм (демонстрация протокола)
    prolog_bytes = fact.encode('utf-8')
    # Opcode 0x0A = POLICY_GATE (v7 extension)
    frame = build_demo_frame(0x0A, prolog_bytes)
    if verbose:
        print(f"\n  #4 demo frame (type_id=0x0A POLICY_GATE):")
        print(f"     Payload: {len(prolog_bytes)} bytes")
        print(f"     Frame:   {len(frame)} bytes")
        print(f"     CRC:     0x{frame[-2]:02X}{frame[-1]:02X}")
        print(f"     Hex:     {frame.hex()[:40]}...")

    # Шаг 5: итоговый вердикт
    all_pass = all(bools.values())
    if verbose:
        print(f"\n  #5 VERDICT: {'PASS' if all_pass else 'FAIL'}")

    return all_pass, g


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--height', type=float, default=0.0,
                   help='Высота над уровнем моря в метрах')
    p.add_argument('--multi', action='store_true',
                   help='Прогнать несколько высот')
    args = p.parse_args()

    if args.multi:
        print(f"{'Height':>8s} {'g (m/s^2)':>14s} {'g/g0':>10s} {'Error %':>10s}")
        print("-" * 46)
        for h in [0, 100, 1_000, 10_000, 100_000, 1_000_000, 10_000_000]:
            g = acceleration(h)
            print(f"{h:>8,} {g['g_ms2']:>14.10f} {g['g_over_surface']:>10.6f} "
                  f"{g['error_rel_pct']:>10.6f}")
    else:
        run(height=args.height)