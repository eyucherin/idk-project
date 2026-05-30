# tools/python/business_tools.py
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from ibm_watsonx_orchestrate.agent_builder.tools import tool

load_dotenv()

_STATE_FILE = Path(
    os.environ.get(
        "TURBINE_STATE_FILE",
        str(Path(__file__).parent.parent.parent / "data" / "turbine_state.json"),
    )
)

# Module-level state shared across all tool calls in the same process
_RUNTIME_STATE: dict = {}

_INITIAL_SENSORS = {
    "WT-01": {"main_bearing_temp_c": 115.0, "gearbox_temp_c": 58.3, "tower_vibration_hz": 2.1, "rotor_rpm": 13.2, "power_output_kw": 1725.0, "nacelle_temp_c": 39.4},
    "WT-02": {"main_bearing_temp_c": 74.9, "gearbox_temp_c": 63.5, "tower_vibration_hz": 3.8, "rotor_rpm": 14.8, "power_output_kw": 1890.0, "nacelle_temp_c": 44.1},
    "WT-03": {"main_bearing_temp_c": 82.4, "gearbox_temp_c": 69.7, "tower_vibration_hz": 5.6, "rotor_rpm": 15.1, "power_output_kw": 1935.0, "nacelle_temp_c": 47.3},
    "WT-04": {"main_bearing_temp_c": 69.2, "gearbox_temp_c": 60.4, "tower_vibration_hz": 2.9, "rotor_rpm": 12.7, "power_output_kw": 1655.0, "nacelle_temp_c": 41.0},
    "WT-05": {"main_bearing_temp_c": 77.1, "gearbox_temp_c": 71.3, "tower_vibration_hz": 4.7, "rotor_rpm": 14.5, "power_output_kw": 1810.0, "nacelle_temp_c": 46.8},
}

_BASE_RECORDS = {
    "WT-01": {"timestamp": "2026-05-28T08:15:00Z", "operating_hours_ytd": 3380},
    "WT-02": {"timestamp": "2026-05-28T08:45:00Z", "operating_hours_ytd": 3210},
    "WT-03": {"timestamp": "2026-05-28T09:30:00Z", "operating_hours_ytd": 2870},
    "WT-04": {"timestamp": "2026-05-28T10:10:00Z", "operating_hours_ytd": 3420},
    "WT-05": {"timestamp": "2026-05-28T11:00:00Z", "operating_hours_ytd": 3050},
}

# (warning_threshold, critical_threshold) for each monitored sensor
_THRESHOLDS: dict[str, tuple[float, float]] = {
    "main_bearing_temp_c": (70.0, 80.0),
    "gearbox_temp_c":      (65.0, 75.0),
    "tower_vibration_hz":  (3.5,  5.0),
    "nacelle_temp_c":      (45.0, 55.0),
    "rotor_rpm":           (17.0, 20.0),  # overspeed thresholds; 0 rpm = OFFLINE
    "power_output_kw":     (500.0, 0.0),  # sentinel — classified separately as OFFLINE when 0
}

# South Korea grid average emission factor (kg CO2 per kWh, based on KEA 2023 data)
_GRID_EMISSION_FACTOR_KG_PER_KWH = 0.4594


def _load_state() -> dict:
    if not _RUNTIME_STATE:
        if _STATE_FILE.exists():
            _RUNTIME_STATE.update(json.loads(_STATE_FILE.read_text()))
        else:
            _RUNTIME_STATE.update({tid: dict(s) for tid, s in _INITIAL_SENSORS.items()})
    return _RUNTIME_STATE


def _save_state(state: dict) -> None:
    # Serialize first (in case state IS _RUNTIME_STATE)
    serialized = json.dumps(state, indent=2)
    _RUNTIME_STATE.clear()
    _RUNTIME_STATE.update(json.loads(serialized))
    try:
        _STATE_FILE.write_text(serialized)
    except Exception:
        pass


def _classify(value: float, warn: float, crit: float) -> str:
    if value >= crit:
        return "CRITICAL"
    if value >= warn:
        return "WARNING"
    return "NORMAL"


def _compute_status(sensors: dict) -> dict:
    status = {}
    for sensor, (warn, crit) in _THRESHOLDS.items():
        if sensor not in sensors:
            continue
        value = sensors[sensor]
        if sensor == "rotor_rpm":
            status[sensor] = "OFFLINE" if value == 0.0 else _classify(value, warn, crit)
        elif sensor == "power_output_kw":
            status[sensor] = "OFFLINE" if value == 0.0 else "NORMAL"
        else:
            status[sensor] = _classify(value, warn, crit)
    return status


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------

@tool
def get_turbine_status(turbine_id: str) -> dict:
    """
    Return the current sensor readings, operating hours, and computed health status
    for a wind turbine.

    Status is derived from sensor thresholds:
      main_bearing_temp_c  — WARNING >= 70 C, CRITICAL >= 80 C
      gearbox_temp_c       — WARNING >= 65 C, CRITICAL >= 75 C
      tower_vibration_hz   — WARNING >= 3.5 Hz, CRITICAL >= 5.0 Hz

    Args:
        turbine_id (str): Turbine identifier, e.g. "WT-01".

    Returns:
        dict: turbine_id, timestamp, operating_hours_ytd, sensors, and per-sensor status,
              or error if the turbine is not found.
    """
    state = _load_state()
    if turbine_id not in state:
        return {"error": f"turbine '{turbine_id}' not found"}
    sensors = state[turbine_id]
    base = _BASE_RECORDS[turbine_id]
    return {
        "turbine_id": turbine_id,
        "timestamp": base["timestamp"],
        "operating_hours_ytd": base["operating_hours_ytd"],
        "sensors": sensors,
        "status": _compute_status(sensors),
    }


# ---------------------------------------------------------------------------
# Action tools — persist mutations to disk
# ---------------------------------------------------------------------------

@tool
def shutdown_turbine(turbine_id: str) -> dict:
    """
    Shut down a wind turbine. Zeroes rotor RPM, power output, and vibration,
    and cools all temperature sensors to ambient levels.

    Args:
        turbine_id (str): Turbine identifier, e.g. "WT-01".

    Returns:
        dict: turbine_id, action, status, updated sensors, or error if not found.
    """
    state = _load_state()
    if turbine_id not in state:
        return {"error": f"turbine '{turbine_id}' not found"}

    state[turbine_id].update({
        "rotor_rpm": 0.0,
        "power_output_kw": 0.0,
        "tower_vibration_hz": 0.0,
        "main_bearing_temp_c": 25.0,
        "gearbox_temp_c": 22.0,
        "nacelle_temp_c": 20.0,
    })
    _save_state(state)

    return {
        "turbine_id": turbine_id,
        "action": "shutdown",
        "status": "offline",
        "sensors": state[turbine_id],
    }


@tool
def restart_turbine(turbine_id: str, cooldown_seconds: int) -> dict:
    """
    Restart a wind turbine after a cooldown period for high-temperature faults.
    Reduces main_bearing_temp_c by 30 C and restores normal operational values.

    Args:
        turbine_id (str): Turbine identifier, e.g. "WT-01".
        cooldown_seconds (int): Cooldown duration specified by the user (logged only).

    Returns:
        dict: turbine_id, action, cooldown_seconds, updated sensors, or error if not found.
    """
    state = _load_state()
    if turbine_id not in state:
        return {"error": f"turbine '{turbine_id}' not found"}

    current_bearing_temp = state[turbine_id].get("main_bearing_temp_c", 0.0)
    cooled_bearing_temp = round(max(0.0, current_bearing_temp - 30.0), 1)

    state[turbine_id].update({
        "rotor_rpm": 13.0,
        "power_output_kw": 1700.0,
        "tower_vibration_hz": 2.5,
        "main_bearing_temp_c": cooled_bearing_temp,
        "nacelle_temp_c": 35.0,
    })
    _save_state(state)

    return {
        "turbine_id": turbine_id,
        "action": "restart",
        "cooldown_seconds": cooldown_seconds,
        "status": "online",
        "main_bearing_temp_c_before": current_bearing_temp,
        "main_bearing_temp_c_after": cooled_bearing_temp,
        "sensors": state[turbine_id],
    }



@tool
def reset_turbine_data(turbine_id: str) -> dict:
    """
    Reset a turbine's sensor data back to its original mock values.
    Useful for restarting a demo scenario after actions have been taken.

    Args:
        turbine_id (str): Turbine identifier, e.g. "WT-01", or "ALL" to reset every turbine.

    Returns:
        dict: confirmation of reset, or error if turbine not found.
    """
    state = _load_state()

    if turbine_id == "ALL":
        for tid in _INITIAL_SENSORS:
            state[tid] = dict(_INITIAL_SENSORS[tid])
        _save_state(state)
        return {"action": "reset", "turbines": list(_INITIAL_SENSORS.keys())}

    if turbine_id not in _INITIAL_SENSORS:
        return {"error": f"turbine '{turbine_id}' not found"}

    state[turbine_id] = dict(_INITIAL_SENSORS[turbine_id])
    _save_state(state)
    return {
        "turbine_id": turbine_id,
        "action": "reset",
        "sensors": state[turbine_id],
    }


# ---------------------------------------------------------------------------
# Recommendation engine
# ---------------------------------------------------------------------------

@tool
def get_recommendations(turbine_id: str) -> dict:
    """
    Classify the health of each sensor for a wind turbine and return the overall
    priority and suggested control action. Does not include recommendation text —
    detailed procedures should be retrieved from the knowledge base by the
    documentation_specialist using the alerting_sensors as the search context.

    Applies nacelle temperature correction (+5 C tolerance on bearing thresholds
    when nacelle_temp_c > 30 C).

    Args:
        turbine_id (str): Turbine identifier, e.g. "WT-01".

    Returns:
        dict: turbine_id, overall_priority, suggested_action,
              nacelle_correction_applied, alerting_sensors (list of sensor/value/status),
              or error if the turbine is not found.
    """
    state = _load_state()
    if turbine_id not in state:
        return {"error": f"turbine '{turbine_id}' not found"}

    sensors = state[turbine_id]

    nacelle_correction = 5.0 if sensors.get("nacelle_temp_c", 0.0) > 30.0 else 0.0
    adjusted_thresholds = {
        sensor: (warn + nacelle_correction, crit + nacelle_correction)
        if sensor in ("main_bearing_temp_c", "gearbox_temp_c")
        else (warn, crit)
        for sensor, (warn, crit) in _THRESHOLDS.items()
    }

    alerting_sensors = []
    overall_priority = "NORMAL"

    for sensor, (warn, crit) in adjusted_thresholds.items():
        value = sensors.get(sensor)
        if value is None:
            continue

        if sensor == "rotor_rpm":
            status = "OFFLINE" if value == 0.0 else _classify(value, warn, crit)
        elif sensor == "power_output_kw":
            status = "OFFLINE" if value == 0.0 else "NORMAL"
        else:
            status = _classify(value, warn, crit)

        if status in ("NORMAL", "OFFLINE"):
            continue
        if status == "CRITICAL":
            overall_priority = "CRITICAL"
        elif status == "WARNING" and overall_priority == "NORMAL":
            overall_priority = "WARNING"
        alerting_sensors.append({"sensor": sensor, "value": value, "status": status})

    statuses = {a["sensor"]: a["status"] for a in alerting_sensors}
    if statuses.get("tower_vibration_hz") == "CRITICAL" or statuses.get("rotor_rpm") == "CRITICAL":
        suggested_action = "shutdown_turbine"
    elif statuses.get("gearbox_temp_c") == "CRITICAL" or statuses.get("nacelle_temp_c") == "CRITICAL":
        suggested_action = "shutdown_turbine"
    elif statuses.get("main_bearing_temp_c") == "CRITICAL":
        suggested_action = "restart_turbine (cooldown 300s)"
    elif overall_priority == "WARNING":
        suggested_action = "inspect_and_monitor"
    else:
        suggested_action = "none"

    return {
        "turbine_id": turbine_id,
        "overall_priority": overall_priority,
        "suggested_action": suggested_action,
        "nacelle_correction_applied": nacelle_correction > 0,
        "alerting_sensors": alerting_sensors,
    }


# ---------------------------------------------------------------------------
# Fleet monitoring tools
# ---------------------------------------------------------------------------

@tool
def get_active_alerts() -> dict:
    """
    Scan all turbines and return only those with one or more WARNING or CRITICAL sensors.
    Results are sorted with CRITICAL turbines first.

    Returns:
        dict: total_alerts count and a list of alerting turbines, each with turbine_id,
              the affected sensors and their severity, and the worst-case severity level.
    """
    state = _load_state()
    alerts = []
    for turbine_id, sensors in state.items():
        status = _compute_status(sensors)
        active = {sensor: level for sensor, level in status.items() if level != "NORMAL"}
        if active:
            worst = "CRITICAL" if "CRITICAL" in active.values() else "WARNING"
            alerts.append({
                "turbine_id": turbine_id,
                "alerting_sensors": active,
                "worst_severity": worst,
            })
    alerts.sort(key=lambda a: (0 if a["worst_severity"] == "CRITICAL" else 1))
    return {
        "total_alerts": len(alerts),
        "alerts": alerts,
    }



@tool
def estimate_carbon_offset(turbine_id: str) -> dict:
    """
    Estimate the CO2 emissions avoided year-to-date by a turbine or the entire fleet.

    Uses the South Korea grid average emission factor (0.4594 kg CO2/kWh, KEA 2023)
    to convert energy produced into equivalent grid emissions displaced.

    Args:
        turbine_id (str): Turbine identifier, e.g. "WT-01", or "ALL" for the full fleet.

    Returns:
        dict: energy produced, CO2 offset in kg and tonnes, and per-turbine breakdown
              when turbine_id is "ALL", or error if the turbine is not found.
    """
    state = _load_state()

    if turbine_id == "ALL":
        per_turbine = []
        fleet_total_kwh = 0.0
        for tid, base in _BASE_RECORDS.items():
            power_kw = state[tid]["power_output_kw"]
            hours_ytd = base["operating_hours_ytd"]
            energy_kwh = power_kw * hours_ytd
            co2_kg = energy_kwh * _GRID_EMISSION_FACTOR_KG_PER_KWH
            fleet_total_kwh += energy_kwh
            per_turbine.append({
                "turbine_id": tid,
                "energy_kwh_ytd": round(energy_kwh, 2),
                "co2_offset_kg": round(co2_kg, 2),
                "co2_offset_tonnes": round(co2_kg / 1000, 3),
            })
        fleet_co2_kg = fleet_total_kwh * _GRID_EMISSION_FACTOR_KG_PER_KWH
        return {
            "scope": "fleet",
            "grid_emission_factor_kg_per_kwh": _GRID_EMISSION_FACTOR_KG_PER_KWH,
            "fleet_total_energy_kwh": round(fleet_total_kwh, 2),
            "fleet_co2_offset_kg": round(fleet_co2_kg, 2),
            "fleet_co2_offset_tonnes": round(fleet_co2_kg / 1000, 3),
            "per_turbine": per_turbine,
        }

    if turbine_id not in state:
        return {"error": f"turbine '{turbine_id}' not found"}

    power_kw = state[turbine_id]["power_output_kw"]
    hours_ytd = _BASE_RECORDS[turbine_id]["operating_hours_ytd"]
    energy_kwh = power_kw * hours_ytd
    co2_kg = energy_kwh * _GRID_EMISSION_FACTOR_KG_PER_KWH

    return {
        "turbine_id": turbine_id,
        "grid_emission_factor_kg_per_kwh": _GRID_EMISSION_FACTOR_KG_PER_KWH,
        "energy_kwh_ytd": round(energy_kwh, 2),
        "co2_offset_kg": round(co2_kg, 2),
        "co2_offset_tonnes": round(co2_kg / 1000, 3),
    }


# ---------------------------------------------------------------------------
# Economics / energy tools
# ---------------------------------------------------------------------------

@tool
def turbine_economics_calculator(
    turbine_id: str,
    electricity_price_per_kwh: float,
    maintenance_cost_krw: float = 0.0,
) -> dict:
    """
    Calculate year-to-date revenue and net economics for a wind turbine.

    Uses the turbine's recorded operating_hours_ytd and current power_output_kw
    to compute actual energy produced and resulting revenue since the start of the year.

    Args:
        turbine_id (str): Turbine identifier, e.g. "WT-01".
        electricity_price_per_kwh (float): Electricity sale price in KRW per kWh.
        maintenance_cost_krw (float): Total YTD maintenance cost in KRW. Defaults to 0.

    Returns:
        dict: turbine_id, power_output_kw, operating_hours_ytd, energy_kwh_ytd,
              gross_revenue_krw, maintenance_cost_krw, net_revenue_krw,
              revenue_per_hour_krw, or error if inputs are invalid or turbine not found.
    """
    if electricity_price_per_kwh <= 0:
        return {"error": "electricity_price_per_kwh must be positive"}
    if maintenance_cost_krw < 0:
        return {"error": "maintenance_cost_krw must be non-negative"}

    state = _load_state()
    if turbine_id not in state:
        return {"error": f"turbine '{turbine_id}' not found"}

    power_kw = state[turbine_id]["power_output_kw"]
    hours_ytd = _BASE_RECORDS[turbine_id]["operating_hours_ytd"]
    energy_kwh_ytd = power_kw * hours_ytd
    gross_revenue = energy_kwh_ytd * electricity_price_per_kwh
    net_revenue = gross_revenue - maintenance_cost_krw

    return {
        "turbine_id": turbine_id,
        "power_output_kw": power_kw,
        "operating_hours_ytd": hours_ytd,
        "energy_kwh_ytd": round(energy_kwh_ytd, 2),
        "gross_revenue_krw": round(gross_revenue),
        "maintenance_cost_krw": round(maintenance_cost_krw),
        "net_revenue_krw": round(net_revenue),
        "revenue_per_hour_krw": round(gross_revenue / hours_ytd),
    }


@tool
def calculate_turbine_energy(turbine_id: str) -> dict:
    """
    Calculate the total energy generated by a wind turbine year-to-date.

    Args:
        turbine_id (str): Turbine identifier, e.g. "WT-01".

    Returns:
        dict: turbine_id, power_output_kw, operating_hours_ytd, total_energy_kwh,
              total_energy_mwh, or error if the turbine is not found.
    """
    state = _load_state()
    if turbine_id not in state:
        return {"error": f"turbine '{turbine_id}' not found"}

    power_kw = state[turbine_id]["power_output_kw"]
    hours_ytd = _BASE_RECORDS[turbine_id]["operating_hours_ytd"]
    total_energy_kwh = power_kw * hours_ytd

    return {
        "turbine_id": turbine_id,
        "power_output_kw": power_kw,
        "operating_hours_ytd": hours_ytd,
        "total_energy_kwh": round(total_energy_kwh, 2),
        "total_energy_mwh": round(total_energy_kwh / 1000, 3),
    }


