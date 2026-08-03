"""
Live2D character control tool — lets the LLM control soyo's expressions and motions.

Sends commands via WebSocket to the Live2D Electron window (ws://127.0.0.1:9190).
"""

import json
import logging
import asyncio

logger = logging.getLogger("hermes.tools.live2d_control")

AVAILABLE_EXPRESSIONS = {
    "angry": ["angry01", "angry02", "angry03", "angry04"],
    "sad": ["sad01", "sad02", "sad03"],
    "cry": ["cry01", "cry02"],
    "smile": ["smile01", "smile02", "smile03", "smile04", "smile05", "smile06"],
    "wink": ["wink01"],
    "serious": ["serious01", "serious02", "serious03", "serious04"],
    "surprised": ["surprised01"],
    "thinking": ["thinking01", "thinking02"],
    "default": ["default", "idle01"],
    "shame": ["shame01", "shame02"],
    "kime": ["kime01"],
    "relieved": ["ando01"],
    "nervous": ["odoodo01"],
}

AVAILABLE_MOTIONS = {
    "angry": ["angry02", "angry03", "angry04"],
    "sad": ["sad01", "sad02"],
    "smile": ["smile01", "smile02", "smile03", "smile04", "smile05", "smile06"],
    "wink": ["wink01"],
    "serious": ["serious01", "serious02", "kime01"],
    "surprised": ["surprised01"],
    "thinking": ["thinking01", "thinking02_01", "thinking02_02"],
    "idle": ["idle01", "nf01", "nf02", "nf03", "nf04", "nf05"],
    "bow": ["bye01", "bye02"],
    "relieved": ["ando01"],
    "nervous": ["odoodo01"],
    "cry": ["cry01", "cry02"],
    "shame": ["shame01", "shame02"],
}


def _send_to_live2d(data: dict) -> bool:
    """Send a command to the Live2D window via HTTP POST."""
    try:
        import urllib.request
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:19919/cmd",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.status == 200
    except Exception as e:
        logger.debug("Live2D HTTP send failed (window may be closed): %s", e)
        return False


def live2d_control_tool(action: str, category: str = None, name: str = None, params: dict = None) -> str:
    """
    Control the Live2D character's expressions and motions.

    Parameters:
        action: What to do — "expression", "motion", "emote", "custom", or "auto".
        category: For "emote" — emotion category.
                  For "expression"/"motion" — the expression/motion category to pick from.
        name: Specific expression/motion name.
        params: For "custom" — dict of parameter name → value (float -2..2).
    """
    import random

    if action == "auto":
        mode = name or "on"
        return json.dumps({
            "success": True,
            "message": f"Live2D auto mode {'enabled' if mode == 'on' else 'disabled'}",
        }, ensure_ascii=False)

    if action == "emote":
        if not category:
            return json.dumps({"success": False, "error": "emote requires a 'category' (e.g. smile, sad, angry, cry, surprised, thinking, shame)"})
        cat = category.lower()
        exps = AVAILABLE_EXPRESSIONS.get(cat, [cat])
        mots = AVAILABLE_MOTIONS.get(cat, [cat])
        expression = random.choice(exps)
        motion = random.choice(mots) if mots else None

        sent_exp = _send_to_live2d({"type": "expression", "name": f"soyo/{expression}"})
        sent_mot = _send_to_live2d({"type": "motion", "name": f"soyo/{motion}"}) if motion else False

        return json.dumps({
            "success": sent_exp or sent_mot,
            "expression": f"soyo/{expression}",
            "motion": f"soyo/{motion}" if motion else None,
            "action": "emote",
            "category": category,
        }, ensure_ascii=False)

    if action == "expression":
        if name:
            exp_name = name
        elif category:
            exps = AVAILABLE_EXPRESSIONS.get(category, [category])
            exp_name = random.choice(exps)
        else:
            return json.dumps({"success": False, "error": "expression requires 'name' or 'category'"})

        sent = _send_to_live2d({"type": "expression", "name": f"soyo/{exp_name}"})
        return json.dumps({"success": sent, "action": "expression", "name": f"soyo/{exp_name}"}, ensure_ascii=False)

    if action == "motion":
        if name:
            mot_name = name
        elif category:
            mots = AVAILABLE_MOTIONS.get(category, [category])
            mot_name = random.choice(mots) if mots else None
            if not mot_name:
                return json.dumps({"success": False, "error": f"No motions found for category '{category}'"})
        else:
            return json.dumps({"success": False, "error": "motion requires 'name' or 'category'"})

        sent = _send_to_live2d({"type": "motion", "name": f"soyo/{mot_name}"})
        return json.dumps({"success": sent, "action": "motion", "name": f"soyo/{mot_name}"}, ensure_ascii=False)

    if action == "custom":
        custom_params = params or {}
        if not custom_params or not isinstance(custom_params, dict):
            return json.dumps({"success": False, "error": "custom requires a 'params' dict"})

        PARAM_MAP = {
            # Legacy Cubism 2 IDs (from expression files)
            "eye_l_open": "PARAM_EYE_L_OPEN",
            "eye_r_open": "PARAM_EYE_R_OPEN",
            "eye_l_smile": "PARAM_EYE_L_SMILE",
            "eye_r_smile": "PARAM_EYE_R_SMILE",
            "eye_form": "PARAM_EYE_FORM",
            "eye_scale": "PARAM_EYE_SCALE",
            "eye_highlight": "PARAM_EYE_HIGHLIGHT",
            "eyelid_l": "PARAM_EYELID_L",
            "eyelid_r": "PARAM_EYELID_R",
            "brow_l_x": "PARAM_BROW_L_X",
            "brow_r_x": "PARAM_BROW_R_X",
            "brow_l_y": "PARAM_BROW_L_Y",
            "brow_r_y": "PARAM_BROW_R_Y",
            "brow_l_angle": "PARAM_BROW_L_ANGLE",
            "brow_r_angle": "PARAM_BROW_R_ANGLE",
            "brow_l_form": "PARAM_BROW_L_FORM",
            "brow_r_form": "PARAM_BROW_R_FORM",
            "mouth_open": "PARAM_MOUTH_OPEN_Y",
            "mouth_form": "PARAM_MOUTH_FORM_01",
            "mouth_scale": "PARAM_MOUTH_SCALE",
            "cheek": "PARAM_CHEEK",
            "cheek2": "PARAM_CHEEK2",
            "tear": "PARAM_TEAR",
            # Standard Cubism SDK parameter IDs
            "ParamEyeLOpen": "ParamEyeLOpen",
            "ParamEyeROpen": "ParamEyeROpen",
            "ParamEyeLSmile": "ParamEyeLSmile",
            "ParamEyeRSmile": "ParamEyeRSmile",
            "ParamBrowLX": "ParamBrowLX",
            "ParamBrowRX": "ParamBrowRX",
            "ParamBrowLY": "ParamBrowLY",
            "ParamBrowRY": "ParamBrowRY",
            "ParamBrowLAngle": "ParamBrowLAngle",
            "ParamBrowRAngle": "ParamBrowRAngle",
            "ParamBrowLForm": "ParamBrowLForm",
            "ParamBrowRForm": "ParamBrowRForm",
            "ParamMouthOpenY": "ParamMouthOpenY",
            "ParamMouthForm": "ParamMouthForm",
            "ParamAngleX": "ParamAngleX",
            "ParamAngleY": "ParamAngleY",
            "ParamAngleZ": "ParamAngleZ",
            "ParamBodyAngleX": "ParamBodyAngleX",
            "ParamBodyAngleY": "ParamBodyAngleY",
            "ParamBodyAngleZ": "ParamBodyAngleZ",
            "ParamArmLA": "ParamArmLA",
            "ParamArmLB": "ParamArmLB",
            "ParamArmRA": "ParamArmRA",
            "ParamArmRB": "ParamArmRB",
            "ParamHandL": "ParamHandL",
            "ParamHandR": "ParamHandR",
            "ParamHairFront": "ParamHairFront",
            "ParamHairSide": "ParamHairSide",
            "ParamHairBack": "ParamHairBack",
            "ParamHairFluffy": "ParamHairFluffy",
            "ParamShoulderY": "ParamShoulderY",
            "ParamBustX": "ParamBustX",
            "ParamBustY": "ParamBustY",
            "ParamEyeBallX": "ParamEyeBallX",
            "ParamEyeBallY": "ParamEyeBallY",
            "ParamBreath": "ParamBreath",
            "ParamBaseX": "ParamBaseX",
            "ParamBaseY": "ParamBaseY",
            "ParamCheek": "ParamCheek",
        }

        resolved = {}
        for key, val in custom_params.items():
            pid = PARAM_MAP.get(key, key)
            try:
                resolved[pid] = float(val)
            except (ValueError, TypeError):
                return json.dumps({"success": False, "error": f"Invalid value for {key}: {val}"})

        sent = _send_to_live2d({"type": "custom", "params": resolved})
        return json.dumps({
            "success": sent,
            "action": "custom",
            "params": custom_params,
        }, ensure_ascii=False)

    return json.dumps({"success": False, "error": f"Unknown action: {action}. Use 'expression', 'motion', 'emote', 'custom', or 'auto'."})



LIVE2D_CONTROL_SCHEMA = {
    "name": "live2d_control",
    "description": (
        "Control the Live2D character's facial expressions and body motions in real-time. "
        "Use this to make the character's expressions match the tone of your response. "
        "Available emotion categories: smile, sad, angry, cry, surprised, thinking, shame, "
        "serious, wink, relieved, nervous, default. "
        "You should call this BEFORE sending your text reply so the expression matches your words."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["expression", "motion", "emote", "custom", "auto"],
                "description": (
                    "'emote': Set expression + play motion from emotion category\n"
                    "'expression': Set facial expression only\n"
                    "'motion': Play body animation once\n"
                    "'custom': Set raw Live2D parameters for precise control (see params)\n"
                    "'auto': Enable/disable auto mode"
                ),
            },
            "category": {
                "type": "string",
                "description": (
                    "For 'emote'/'expression'/'motion' — the emotion category: "
                    "smile, sad, angry, cry, surprised, thinking, shame, serious, wink, relieved, nervous, default. "
                    "A random expression/motion variant from the category will be picked."
                ),
            },
            "name": {
                "type": "string",
                "description": (
                    "For 'expression' — a specific expression name: wink01, angry02, sad01, smile04, thinking01, "
                    "serious01, surprised01, cry01, shame01, kime01, ando01, odoodo01, default, idle01. "
                    "For 'motion' — a specific motion name from the same set plus bye01, nf01-nf05. "
                    "For 'auto' — 'on' or 'off'."
                ),
            },
            "params": {
                "type": "object",
                "description": (
                    "For 'custom' action only — dict mapping parameter names to float values (-2 to 2).\n"
                    "Available params: eye_l_open, eye_r_open, eye_l_smile, eye_r_smile, eye_form, eye_scale, "
                    "eye_highlight, eyelid_l, eyelid_r, brow_l_x, brow_r_x, brow_l_y, brow_r_y, brow_l_angle, "
                    "brow_r_angle, brow_l_form, brow_r_form, mouth_open, mouth_form, mouth_scale, cheek, cheek2, tear.\n"
                    "Example: {\"eye_l_open\": 0.3, \"mouth_form\": 0.5, \"brow_l_y\": -0.8} for a narrow-eyed smile."
                ),
            },
        },
        "required": ["action"],
    },
}


def check_live2d_control_requirements() -> bool:
    """Live2D control is always available — just gracefully fails if window isn't open."""
    return True


from tools.registry import registry

registry.register(
    name="live2d_control",
    toolset="live2d",
    schema=LIVE2D_CONTROL_SCHEMA,
    handler=lambda args, **kw: live2d_control_tool(
        action=args.get("action", "emote"),
        category=args.get("category"),
        name=args.get("name"),
        params=args.get("params"),
    ),
    check_fn=check_live2d_control_requirements,
    emoji="🎭",
)
