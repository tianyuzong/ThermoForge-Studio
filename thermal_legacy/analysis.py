import math
import numpy as np


def lumped_temperature(t, mass, cp, area, h, ambient, initial, power):
    capacity = mass * cp
    conductance = max(0.0, h * area)
    if conductance < 1e-15:
        return initial + power * t / capacity
    e = math.exp(-conductance * t / capacity)
    return ambient + power / conductance + (initial - ambient - power / conductance) * e


def fit_h(request):
    points = sorted(request.measurements, key=lambda x: x.time_s)
    low, high = sorted(request.h_bounds)
    for _ in range(90):
        mid = (low + high) / 2
        # A one-parameter least-squares search is robust for noisy temperature data.
        delta = max(mid * 1e-4, 1e-6)
        left = sum((lumped_temperature(p.time_s, request.mass_kg, request.cp_J_kgK, request.area_m2, mid - delta, request.ambient_C, request.initial_C, request.power_W) - p.temperature_C) ** 2 for p in points)
        right = sum((lumped_temperature(p.time_s, request.mass_kg, request.cp_J_kgK, request.area_m2, mid + delta, request.ambient_C, request.initial_C, request.power_W) - p.temperature_C) ** 2 for p in points)
        if left < right:
            high = mid
        else:
            low = mid
    h = (low + high) / 2
    residual = math.sqrt(sum((lumped_temperature(p.time_s, request.mass_kg, request.cp_J_kgK, request.area_m2, h, request.ambient_C, request.initial_C, request.power_W) - p.temperature_C) ** 2 for p in points) / len(points))
    return dict(mode='fit_h', h_W_m2K=h, rmse_C=residual, predictions=[dict(time_s=p.time_s, measured_C=p.temperature_C, predicted_C=lumped_temperature(p.time_s, request.mass_kg, request.cp_J_kgK, request.area_m2, h, request.ambient_C, request.initial_C, request.power_W)) for p in points])


def power_limit(request):
    h = request.h_W_m2K
    low, high = 0.0, max(request.power_W, 1.0)
    while lumped_temperature(request.duration_s, request.mass_kg, request.cp_J_kgK, request.area_m2, h, request.ambient_C, request.initial_C, high) < request.max_temperature_C and high < 1e12:
        high *= 2
    for _ in range(90):
        mid = (low + high) / 2
        if lumped_temperature(request.duration_s, request.mass_kg, request.cp_J_kgK, request.area_m2, h, request.ambient_C, request.initial_C, mid) <= request.max_temperature_C:
            low = mid
        else:
            high = mid
    power = low
    return dict(mode='power_limit', h_W_m2K=h, maximum_power_W=power, predicted_temperature_C=lumped_temperature(request.duration_s, request.mass_kg, request.cp_J_kgK, request.area_m2, h, request.ambient_C, request.initial_C, power), duration_s=request.duration_s)


def run_calibration(request):
    return fit_h(request) if request.mode == 'fit_h' else power_limit(request)


def run_cycle(request):
    te = request.evaporating_C + 273.15
    tc = request.condensing_C + 273.15
    carnot = te / (tc - te)
    cop = carnot * request.compressor_efficiency
    compressor_power = request.cooling_capacity_W / cop
    total_power = compressor_power + request.fan_pump_power_W
    return dict(refrigerant=request.refrigerant, evaporating_C=request.evaporating_C, condensing_C=request.condensing_C, suction_C=request.evaporating_C + request.superheat_C, liquid_C=request.condensing_C - request.subcool_C, cooling_capacity_W=request.cooling_capacity_W, compressor_power_W=compressor_power, auxiliary_power_W=request.fan_pump_power_W, total_power_W=total_power, heating_rejection_W=request.cooling_capacity_W + compressor_power, cop=cop, eer_Btu_Wh=cop * 3.412142, model='理想循环温差估算；未调用工质物性库')


def run_cpu(request):
    """Run a standalone CPU / motherboard / cooler thermal network.

    The model is deliberately small enough for interactive use. It is not a
    replacement for CFD: the returned curves expose the assumptions so the UI
    can label this as a system-level estimate next to the 3-D FEM workflow.
    """
    dt = float(request.dt_s)
    steps = max(1, int(math.ceil(request.duration_s / dt)))
    # Cap the explicit step for stiff interface values while preserving the
    # requested output cadence.
    cpu_C = board_C = cooler_C = float(request.initial_C)
    coolant_C = float(request.water_inlet_C)
    c_cpu = request.cpu_mass_kg * request.cpu_cp_J_kgK
    c_board = request.board_mass_kg * request.board_cp_J_kgK
    c_cooler = request.cooler_mass_kg * request.cooler_cp_J_kgK
    g_interface = 1.0 / request.interface_resistance_K_W
    g_board = request.board_h_W_m2K * request.board_area_m2
    m_dot = request.water_flow_L_min / 60.0 / 1000.0  # kg/s, rho ~= 1 kg/L
    g_coolant_flush = m_dot * request.water_cp_J_kgK
    # A short fluid residence time keeps the lumped coolant node responsive.
    c_coolant = max(g_coolant_flush * 2.0, 1e-9)
    times, cpu_values, board_values, cooler_values, coolant_values, removed = [], [], [], [], [], []
    energy_in = energy_out = 0.0
    for i in range(steps + 1):
        t = min(i * dt, float(request.duration_s))
        times.append(t); cpu_values.append(cpu_C); board_values.append(board_C); cooler_values.append(cooler_C); coolant_values.append(coolant_C)
        if i == steps: break
        # Conductive paths from CPU into the board and the cooler interface.
        q_cpu_cooler = g_interface * (cpu_C - cooler_C)
        q_cpu_board = 0.35 * (cpu_C - board_C)  # package-to-socket spreading path
        q_board_ambient = max(0.0, g_board * (board_C - request.ambient_C))
        if request.cooler_type == 'water':
            q_coolant = max(0.0, request.water_cooler_UA_W_K * (cooler_C - coolant_C))
            q_radiator = max(0.0, request.radiator_UA_W_K * (coolant_C - request.ambient_C))
            q_radiator = min(q_radiator, max(0.0, g_coolant_flush * (coolant_C - request.ambient_C)))
            q_flush = max(0.0, g_coolant_flush * (coolant_C - request.water_inlet_C))
            q_cooler_ambient = 0.0
            coolant_derivative = (q_coolant - q_radiator - q_flush) / c_coolant
        else:
            q_coolant = q_radiator = q_flush = 0.0
            q_cooler_ambient = min(max(0.0, request.air_h_W_m2K * request.air_area_m2 * (cooler_C - request.ambient_C)), request.air_capacity_W)
            coolant_derivative = 0.0
        # Forward Euler update. Positive values are heat leaving the node.
        cpu_derivative = (request.cpu_power_W - q_cpu_cooler - q_cpu_board) / c_cpu
        cooler_derivative = (q_cpu_cooler - q_coolant - q_cooler_ambient) / c_cooler
        board_derivative = (q_cpu_board - q_board_ambient) / c_board
        cpu_C += dt * cpu_derivative; cooler_C += dt * cooler_derivative; board_C += dt * board_derivative
        coolant_C += dt * coolant_derivative
        q_out = (q_radiator + q_flush) if request.cooler_type == 'water' else q_cooler_ambient
        # Include motherboard-to-ambient leakage in the reported external
        # removal so the energy balance covers every path out of the network.
        removed.append(q_out + q_board_ambient)
        energy_in += request.cpu_power_W * dt; energy_out += (q_out + q_board_ambient) * dt
    if not removed: removed = [0.0]
    return dict(
        mode='cpu_cooling', cooler_type=request.cooler_type,
        model='集中参数 CPU-主板-散热器热网络；水冷含冷却液与冷排等效换热',
        times_s=times, cpu_C=cpu_values, board_C=board_values,
        cooler_C=cooler_values, coolant_C=coolant_values,
        heat_removed_W=removed + [removed[-1]],
        input_energy_J=energy_in, removed_energy_J=energy_out,
        maximum_cpu_C=max(cpu_values), final_cpu_C=cpu_values[-1],
        final_board_C=board_values[-1], final_cooler_C=cooler_values[-1],
        final_coolant_C=coolant_values[-1],
        stored_energy_J=(c_cpu * (cpu_C - request.initial_C) + c_board * (board_C - request.initial_C) +
                         c_cooler * (cooler_C - request.initial_C) +
                         c_coolant * (coolant_C - request.water_inlet_C)),
        thermal_balance_error_J=energy_in - energy_out - (c_cpu * (cpu_C - request.initial_C) +
                         c_board * (board_C - request.initial_C) + c_cooler * (cooler_C - request.initial_C) +
                         c_coolant * (coolant_C - request.water_inlet_C)),
        assumptions={'cpu_power_W':request.cpu_power_W, 'ambient_C':request.ambient_C,
                     'interface_resistance_K_W':request.interface_resistance_K_W,
                     'cooler_type':request.cooler_type}
    )
