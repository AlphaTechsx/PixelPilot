import io
from typing import Any, Dict, List, Optional
from google import genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field
from config import Config

api_key = Config.GEMINI_API_KEY
model = Config.GEMINI_MODEL
if not api_key:
    raise ValueError("Missing API Key! Set GEMINI_API_KEY in your .env or environment.")

client = genai.Client(api_key=api_key)


class ModelWrapper:
    def __init__(self, model_name):
        self.model_name = model_name

    def generate_content(self, contents, config=None):
        return client.models.generate_content(
            model=self.model_name, contents=contents, config=config
        )


def get_model():
    return ModelWrapper(model)


def create_reference_sheet(crops):
    if not crops:
        return None
    cell_w, cell_h = 100, 100
    cols = 8

    rows = (len(crops) + cols - 1) // cols

    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), color=(30, 30, 30))
    draw = ImageDraw.Draw(sheet)

    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except Exception:
        font = ImageFont.load_default()

    for i, item in enumerate(crops):
        img = item["image"]
        img.thumbnail((cell_w - 10, cell_h - 30))

        c, r = i % cols, i // cols
        x, y = c * cell_w + 5, r * cell_h + 30

        sheet.paste(img, (x, y))
        draw.text((x, y - 25), f"ID:{item['id']}", fill=(0, 255, 0), font=font)

    return sheet


class ActionResponse(BaseModel):
    element_id: int = Field(
        description="The ID number of the element to interact with from labels."
    )
    reasoning: str = Field(description="Reasoning for choosing this specific element.")


class SubAction(BaseModel):
    action_type: str = Field(
        description="The type of action: click, type_text, press_key, key_combo, open_app, wait, magnify"
    )
    params: Dict[str, Any] = Field(
        description="Parameters for the action. For 'click', MUST include 'element_id'. For 'type_text', MUST include 'text'."
    )
    reasoning: str = Field(description="Reasoning for this specific sub-action")


class PlannedAction(BaseModel):
    action_type: str = Field(
        description="The primary action type (or 'sequence' if using action_sequence)"
    )
    params: Dict[str, Any] = Field(description="Parameters for the action")
    reasoning: str = Field(description="Detailed logic for this decision")
    confidence: float = Field(description="Score between 0.0 and 1.0 indicating AI certainty")
    clarification_needed: bool = Field(description="True if the AI needs more info from user")
    clarification_question: Optional[str] = Field(
        description="Question to ask user if clarification_needed is True"
    )
    task_complete: bool = Field(description="True if the user's objective is finished")
    skip_verification: bool = Field(
        default=False,
        description="True if verification/screenshot is unnecessary (e.g., for trivial actions like 'reply' or 'wait')",
    )
    needs_vision: bool = Field(
        default=False,
        description="True if you cannot complete the task without seeing the screen (e.g. need to click buttons, find icons).",
    )
    expected_result: Optional[str] = Field(description="What should happen after this action")
    action_sequence: Optional[List[SubAction]] = Field(
        default=None, description="A sequence of actions to execute at once (Turbo Mode)"
    )


def ask_brain(
    user_command,
    screen_elements,
    original_path,
    debug_path,
    reference_sheet,
    media_resolution: str = "low",
):
    """
    Sends 3 Images to Gemini: Original, Annotated (Green IDs), and Reference (Zoomed).
    Returns a single action decision.
    """
    try:
        clean_image = Image.open(original_path)
        annotated_image = Image.open(debug_path)
    except Exception as e:
        print(f"Error loading images: {e}")
        return None

    elements_str = "\n".join(
        [f"ID {el['id']}: {el['type']} '{el['label']}'" for el in screen_elements[:100]]
    )

    prompt_text = f"""
    You are an AI OS Agent.
    USER COMMAND: "{user_command}"
    
    DETECTED ELEMENTS:
    {elements_str}

    ATTACHMENTS PROVIDED:
    1. [Original Screen]: The user's actual view.
    2. [Annotated Screen]: Green overlays with ID NUMBERS on every clickable element.

    INSTRUCTIONS:
    1. Use [Original Screen] to understand context (e.g., "Where is the Spotify window?").
    2. Use [Annotated Screen] to get the exact ID Number.

    CRITICAL:
    - Return the ID found on the [Annotated Screen] overlay.
    - If targeting a tab/list, pick the ID centered on the text/icon.
    - If there are multiple references that look very similar and confusing for the user command
      (like "Close" buttons), focus on the original screen and the annotated screen to find
      the correct ID.

    Return JSON: {{ "element_id": <int>, "reasoning": "<string>" }}
    """

    contents = [prompt_text, clean_image, annotated_image]

    try:
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": ActionResponse.model_json_schema(),
                "tools": [types.Tool(google_search=types.GoogleSearch())],
            },
        )
        return ActionResponse.model_validate_json(response.text).model_dump()
    except Exception as e:
        print(f"Gemini Error: {e}")
        return None


def plan_task(
    user_command: str,
    screen_elements: List[Dict],
    original_path: str,
    debug_path: str,
    reference_sheet,
    task_context: Optional[str] = None,
    magnification_hint: Optional[str] = None,
    history: Optional[List] = None,
    guidance_mode: bool = False,
    media_resolution: str = "low",
) -> Optional[tuple[Dict[str, Any], Any]]:
    """
    Enhanced brain function for multi-step task planning.
    Returns a structured action plan with multiple steps if needed.

    Args:
        user_command: The user's natural language command
        screen_elements: List of detected UI elements
        original_path: Path to original screenshot
        debug_path: Path to annotated screenshot
        reference_sheet: Reference sheet image
        task_context: Optional context about ongoing task
        magnification_hint: Optional info about current zoom state

    Returns:
        Dict with task plan or None if error
    """
    try:
        clean_image = Image.open(original_path)
        annotated_image = Image.open(debug_path)
    except Exception as e:
        print(f"Error loading images: {e}")
        return None

    elements_str = "\n".join(
        [
            f"ID {el['id']}: {el['type']} '{el['label']}' at ({el['x']}, {el['y']})"
            for el in screen_elements[:150]
        ]
    )

    context_section = ""
    if task_context:
        context_section = f"\nTASK CONTEXT (previous steps):\n{task_context}\n"

    mag_section = ""
    if magnification_hint:
        mag_section = f"\n MAGNIFICATION NOTE: {magnification_hint}\n"

    turbo_status = "ENABLED" if Config.TURBO_MODE else "DISABLED"
    guidance_section = ""
    if guidance_mode:
        guidance_section = """
GUIDANCE MODE (HUMAN-FOLLOWING):
- Prefer manual steps a human can do: open_app, press_key, key_combo, click, type_text.
- Avoid call_skill unless no UI path exists.
- Prefer clicks on OCR text elements that match the user's words (e.g., "Chrome", "Settings", "Search").
- If a label is visible in OCR, choose that element_id instead of a generic control.
- Do not output generic actions like "open system controls" when a specific OCR label exists.
"""

    prompt_text = f"""
You are an advanced AI OS Agent capable of planning and executing tasks using discrete actions or action sequences.

TURBO MODE STATUS: {turbo_status}

USER COMMAND: "{user_command}"
{context_section}{mag_section}
SCREEN ELEMENTS DETECTED (OCR + Visual):
{elements_str}

ATTACHMENTS:
1. [Original Screen]: The user's actual desktop view
2. [Annotated Screen]: Green overlays with ID numbers on clickable elements. USE THIS to find IDs.
3. [Reference Sheet] (Optional): Zoomed view of icons/buttons.

YOUR TASK:
Analyze the user's command and current screen state. Return a JSON plan with the NEXT action(s) to take.

{guidance_section}

AVAILABLE SKILLS (HYBRID MODE):
- The agent has "Skills" that use APIs instead of UI interaction. Use them PREFERENTIALLY for reliability.
- Skill: "media" (or "spotify")
  - method: "play" (params: {{"query": "song name"}}) -> Plays music/media.
  - method: "pause" -> Pauses.
  - method: "next" -> Skips track.
  - method: "previous" -> Previous track.
  - method: "status" -> Gets current media status.
- Skill: "browser"
  - method: "open" (params: {{"url": "google.com"}}) -> Opens URL in default browser.
  - method: "search" (params: {{"query": "search term"}}) -> Opens Google search.
- Skill: "system"
  - method: "volume" (params: {{"action": "up"|"down"|"mute"}}) -> Controls volume.
  - method: "lock" -> Locks the PC.
  - method: "minimize" -> Minimizes all windows (Show Desktop).
  - method: "settings" (params: {{"page": "display"}}) -> Opens Windows Settings.
- Skill: "timer"
  - method: "timer" -> Opens Windows Clock (Timer).
  - method: "alarm" -> Opens Windows Clock (Alarm).
  - method: "stopwatch" -> Opens Windows Clock (Stopwatch).

AVAILABLE ACTIONS:
- click: Click on a UI element by ID. Params: {{"element_id": <int>}}
- type_text: Type text into focused field. Params: {{"text": "<string>"}}
- press_key: Press a keyboard key (enter, tab, esc, win, etc.). Params: {{"key": "<string>"}}
- key_combo: Press key combination. Params: {{"keys": ["ctrl", "c"]}}
- wait: Wait for seconds. Params: {{"seconds": <int>}}
- search_web: Search the web. Params: {{"query": "<string>"}}
- open_app: Open an application via Start menu/Run. Params: {{"app_name": "<string>"}}
- magnify: Zoom in on a specific area to see small icons/text. Params: {{"element_id": <int>, "zoom_level": 2.0}}
- reply: Just answer the user's question directly. Params: {{"text": "<string>"}}
- call_skill: Execute a skill function. Params: {{"skill": "media", "method": "play", "args": {{"query": "..."}}}}

TURBO MODE RULES:
- If {turbo_status} == ENABLED, you SHOULD combine multiple stable steps into 'action_sequence'.
- Example: Click 'Search', Wait 0.5s, Type 'Notepad', Press 'Enter'.
- Do NOT sequence actions if the first action changes the screen drastically (like opening a new window) and you need to see the result before acting.
- In 'action_sequence', the 'action_type' of the parent JSON MUST be "sequence".

RESPONSE FORMAT:
{{
    "action_type": "...", 
    "params": {{ ... }},
    "reasoning": "Explain WHY you chose this action and this ID.",
    "confidence": 0.0-1.0,
    "clarification_needed": false,
    "task_complete": false,
    "skip_verification": false,
    "needs_vision": true,
    "action_sequence": [...]
}}

CRITICAL GUIDELINES:
1. **ID Precision**: You MUST use the `element_id` from the [Annotated Screen] or the provided list. Do not hallucinate IDs.
2. **Launch First**: If the user wants to open an app (e.g., "Open Notepad"), always use `open_app` first. Do not try to find the icon manually unless `open_app` failed previously.
3. **Verification**: Set `task_complete` to true ONLY if you are sure the user's goal is fully achieved.
4. **Efficiency**: For trivial actions (like 'reply', 'wait', or simple confirmations) where a screenshot verification is overkill, SET `skip_verification: true`.
5. **Magnification**: If you cannot see an element clearly or the text is too small, use `magnify` on the approximate area.
6. **Robotics Fallback**: If OCR is failing to find an icon, mention "requesting robotics fallback" in your reasoning.
7. **Blind Mode Switching**: If you are entering a phase where you don't need to see the screen (e.g. typing a long text, waiting, or using skills/hotkeys), SET `needs_vision: false`. This will make the agent run faster by skipping screenshots for the next step.

"""

    def pil_to_part(img, res="low"):
        res_map = {"low": "MEDIA_RESOLUTION_LOW", "high": "MEDIA_RESOLUTION_HIGH"}
        res_enum = res_map.get(res.lower(), "MEDIA_RESOLUTION_LOW")

        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format="PNG")
        return types.Part.from_bytes(
            data=img_byte_arr.getvalue(), mime_type="image/png", media_resolution=res_enum
        )

    contents = [
        types.Part(text=prompt_text),
        pil_to_part(clean_image, res=media_resolution),
        pil_to_part(annotated_image, res=media_resolution),
    ]
    if reference_sheet:
        contents.append(pil_to_part(reference_sheet, res=media_resolution))

    contents_to_send = history[-10:] if history else []
    contents_to_send.append({"role": "user", "parts": contents})

    try:
        response = client.models.generate_content(
            model=model,
            contents=contents_to_send,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": PlannedAction.model_json_schema(),
                "thinking_config": {"thinking_level": "high"},
                "tools": [
                    types.Tool(google_search=types.GoogleSearch()),
                    types.Tool(code_execution=types.ToolCodeExecution()),
                ],
            },
        )

        model_part = response.candidates[0].content
        action_dict = PlannedAction.model_validate_json(response.text).model_dump()

        return action_dict, model_part
    except Exception as e:
        print(f"Error in plan_task: {e}")
        return None


def plan_task_blind(
    user_command: str,
    task_context: Optional[str] = None,
    history: Optional[List] = None,
) -> Optional[tuple[Dict[str, Any], Any]]:
    """
    Planning for 'Blind Mode' (No Vision).
    The agent attempts to solve the task using only OS skills, hotkeys, and shell commands.
    If it realizes it needs to see the screen (e.g. to click a button), it returns needs_vision=True.
    """

    context_section = ""
    if task_context:
        context_section = f"\nTASK CONTEXT (previous steps):\n{task_context}\n"

    turbo_status = "ENABLED" if Config.TURBO_MODE else "DISABLED"

    prompt_text = f"""
You are a 'Blind' AI OS Agent. You can control the computer using keyboard shortcuts, system skills, and commands, BUT YOU CANNOT SEE THE SCREEN.

USER COMMAND: "{user_command}"
{context_section}

YOUR GOAL:
Try to fulfill the user's request using ONLY the available blind tools.
However, you must be extremely CAUTIOUS. "Blind Mode" is efficient but risky.

CRITICAL "FEAR OF FAILURE" PROTOCOL:
1. **Safety First**: If you are not 100% sure that the app is open, focused, and ready for input, REQUEST VISION (`needs_vision: true`). Do not assume state.
2. **Verify Completion**: If you are about to complete a task (like sending a message or saving a file), and you haven't recently seen the screen to confirm it worked, REQUEST VISION to verify.
3. **Future Thinking**: Before performing a blind action, ask: "Will I need to see the result of this immediately?" If yes, switch to vision *now*.
4. **No Guessing**: If the user asks to "click the login button", do NOT try to tab-navigate blindly unless you are extremely confident. Just request vision.
5. **Context Awareness**: If the previous step failed or had low confidence, do NOT continue blindly. Switch to vision.

AVAILABLE BLIND ACTIONS:
- type_text: Type text. Params: {{"text": "..."}} (Assumes correct field is focused!)
- press_key: Press key. Params: {{"key": "win"}}
- key_combo: Key combo. Params: {{"keys": ["ctrl", "c"]}}
- wait: Wait. Params: {{"seconds": 1}}
- open_app: Open app via Run/Start. Params: {{"app_name": "notepad"}}
- search_web: Google search. Params: {{"query": "..."}}
- reply: Answer user. Params: {{"text": "..."}}
- call_skill: Use a skill (Media, Browser, System, Timer). Params: {{"skill": "...", "method": "...", "args": {{...}}}}

UNAVAILABLE ACTIONS (Requires Vision):
- click (You have no coordinates!)
- magnify

RESPONSE RULES:
- Default to Vision: If in doubt, set `needs_vision: true`.
- If the task is "Play music", use call_skill("media", "play", ...).
- If the task is "Open Notepad", use open_app("Notepad").
- If the task is "Click the Submit button", set `needs_vision: true`.
- If the task is "What is on my screen?", set `needs_vision: true`.

RESPONSE FORMAT:
{{
    "action_type": "...",
    "params": {{ ... }},
    "reasoning": "Explain your risk assessment here. Why is blind safe? Or why is vision needed?",
    "needs_vision": false,  <-- SET TO TRUE IF YOU NEED TO SEE, VERIFY, OR CHECK FOR ERRORS
    "task_complete": false,
    "skip_verification": false
}}
"""
    contents = [types.Part(text=prompt_text)]
    contents_to_send = history[-10:] if history else []
    contents_to_send.append({"role": "user", "parts": contents})

    try:
        response = client.models.generate_content(
            model=model,
            contents=contents_to_send,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": PlannedAction.model_json_schema(),
            },
        )
        model_part = response.candidates[0].content
        action_dict = PlannedAction.model_validate_json(response.text).model_dump()
        return action_dict, model_part
    except Exception as e:
        print(f"Error in plan_task_blind: {e}")
        return None
