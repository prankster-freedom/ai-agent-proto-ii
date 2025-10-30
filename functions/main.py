# -*- coding: utf-8 -*-

import firebase_admin
from firebase_admin import credentials, firestore, functions
from firebase_functions import https_fn, options
import vertexai
from vertexai.generative_models import GenerativeModel, ChatSession
import yaml
import datetime

# Initialize Firebase Admin SDK
firebase_admin.initialize_app()

# Initialize Vertex AI
PROJECT_ID = "gma-stg"
LOCATION = "asia-northeast1"
vertexai.init(project=PROJECT_ID, location=LOCATION)

# Set CORS options
options.set_global_options(
    cors=options.CorsOptions(
        cors_origins=[
            "http://localhost:5000", 
            "http://127.0.0.1:5000", 
            f"https://{PROJECT_ID}.web.app"
        ],
        cors_methods=["GET", "POST"],
    )
)

# Constants
GEMINI_MODEL = "gemini-1.5-flash-001"
DB = firestore.client()

# --- Callable Functions ---

@https_fn.on_call()
def chat(req: https_fn.CallableRequest) -> https_fn.Response:
    """Handles a chat message from the user."""
    if not req.auth:
        raise https_fn.HttpsError(code=https_fn.FunctionsErrorCode.UNAUTHENTICATED,
                                  message="Authentication required.")

    uid = req.auth.uid
    text = req.data.get("text", "")
    if not text.strip():
        raise https_fn.HttpsError(code=https_fn.FunctionsErrorCode.INVALID_ARGUMENT,
                                  message="Text input cannot be empty.")

    print(f"Chat request from UID: {uid}, Text: '{text}'")

    try:
        # 1. Get AI Persona and Chat History
        user_ref = DB.collection('users').document(uid)
        persona_ref = user_ref.collection('aiPersona').document('current')
        history_ref = user_ref.collection('chatHistory')

        persona_doc = persona_ref.get()
        if not persona_doc.exists:
            print(f"Persona not found for {uid}, creating default.")
            base_personality = _create_default_persona()
            persona_ref.set({
                'basePersonality': base_personality,
                'updatedAt': firestore.SERVER_TIMESTAMP
            })
        else:
            base_personality = persona_doc.to_dict().get('basePersonality')

        chat_history_docs = history_ref.order_by('timestamp').limit_to_last(50).get()
        
        # 2. Call Gemini API
        model = GenerativeModel(GEMINI_MODEL, system_instruction=[base_personality])
        
        history = []
        for doc in chat_history_docs:
            message = doc.to_dict()
            history.append({"role": message['role'], "parts": [{"text": message['content']}]})
        
        chat_session = ChatSession(model=model, history=history)
        response = chat_session.send_message(text)
        ai_response = response.text

        print(f"Gemini response for {uid}: '{ai_response}'")

        # 3. Save messages to Firestore
        batch = DB.batch()
        user_message_ref = history_ref.document()
        batch.set(user_message_ref, {
            'role': 'user',
            'content': text,
            'timestamp': firestore.SERVER_TIMESTAMP
        })
        ai_message_ref = history_ref.document()
        batch.set(ai_message_ref, {
            'role': 'model',
            'content': ai_response,
            'timestamp': firestore.SERVER_TIMESTAMP
        })
        batch.commit()

        # 4. Trigger Daydream if needed (DR11)
        user_message_count = len([doc for doc in chat_history_docs if doc.to_dict()['role'] == 'user']) + 1
        if user_message_count > 0 and user_message_count % 10 == 0:
            print(f"User message count for {uid} reached {user_message_count}, triggering daydream.")
            # Run analysis in the background. The function will continue after returning the response.
            create_personality_analysis(uid, 'daydream') 

        return https_fn.Response({"text": ai_response})

    except Exception as e:
        print(f"Error in chat function for UID {uid}: {e}")
        raise https_fn.HttpsError(code=https_fn.FunctionsErrorCode.INTERNAL,
                                  message=f"An internal error occurred: {e}")

@https_fn.on_call()
def deleteMemory(req: https_fn.CallableRequest) -> https_fn.Response:
    """Deletes all data for the requesting user."""
    if not req.auth:
        raise https_fn.HttpsError(code=https_fn.FunctionsErrorCode.UNAUTHENTICATED,
                                  message="Authentication required.")

    uid = req.auth.uid
    print(f"Memory deletion request for UID: {uid}")

    try:
        user_ref = DB.collection('users').document(uid)
        # This is a recursive delete, it will delete all subcollections.
        DB.recursive_delete(user_ref)
        print(f"Successfully deleted all data for user {uid}")
        return https_fn.Response({"status": "success"})

    except Exception as e:
        print(f"Error deleting data for user {uid}: {e}")
        raise https_fn.HttpsError(code=https_fn.FunctionsErrorCode.INTERNAL,
                                  message=f"An error occurred: {e}")

# --- Background Functions & Helpers ---

def create_personality_analysis(uid: str, analysis_type: str):
    """Analyzes chat history to extract personality traits (Daydream/Dream)."""
    print(f"Starting personality analysis ({analysis_type}) for UID: {uid}")
    try:
        user_ref = DB.collection('users').document(uid)
        history_ref = user_ref.collection('chatHistory')
        analysis_ref = user_ref.collection('personalityAnalyses').document()

        # 1. Get chat history
        docs = history_ref.order_by('timestamp', direction=firestore.Query.DESCENDING).limit(50).get()
        if not docs:
            print(f"No chat history found for {uid}. Skipping analysis.")
            return
        
        # Format history for the prompt
        conversation_text = "\n".join([
            f"{d.to_dict().get('role')}: {d.to_dict().get('content')}"
            for d in reversed(docs) # reverse to get chronological order
        ])
        
        # 2. Get analysis prompt
        prompt = _get_daydream_prompt()
        full_prompt = f"{prompt}\n\n--- 対話履歴 ---\n{conversation_text}\n--- 分析開始 ---"
        
        # 3. Call Gemini API
        model = GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(full_prompt)
        
        # 4. Parse and save result
        analysis_result = _parse_yaml_from_text(response.text)
        
        if not analysis_result:
             raise ValueError("Failed to parse YAML from Gemini response.")

        analysis_data = {
            'type': analysis_type,
            'timestamp': firestore.SERVER_TIMESTAMP,
            'analysis': analysis_result,
            'sourceHistory': [d.reference.path for d in docs]
        }
        analysis_ref.set(analysis_data)
        print(f"Successfully created and saved personality analysis ({analysis_type}) for UID: {uid}")

    except Exception as e:
        print(f"Error in create_personality_analysis for UID {uid}: {e}")
        # We don't throw HttpsError here because it's a background task.

def _parse_yaml_from_text(text: str):
    """Extracts and parses a YAML block from a string."""
    try:
        # Find the YAML block
        start = text.find('```yaml') + len('```yaml')
        end = text.find('```', start)
        if start == -1 or end == -1:
            # If no ```yaml``` block, try to parse the whole string
            return yaml.safe_load(text)
        
        yaml_str = text[start:end].strip()
        return yaml.safe_load(yaml_str)
    except yaml.YAMLError as e:
        print(f"Error parsing YAML: {e}")
        return None

def _get_daydream_prompt():
    """Returns the system prompt for the daydream personality analysis."""
    return ('''
あなたはプロの心理学者です。提供されたユーザーとの対話履歴を分析し、ビッグファイブ（特性5因子）モデルに基づいてユーザーの性格特性を推定してください。

ビッグファイブの各特性（外向性、協調性、誠実性、神経症的傾向、開放性）について、ユーザーの性格がどの程度現れているかを1-5の5段階で評価し、その評価の根拠となった具体的な対話内容を引用してください。

出力は以下のYAML形式で厳密に記述してください。

```yaml
big_five:
  openness:
    score: [1-5の5段階評価]
    reason: "[評価の根拠となる短い説明]"
    evidence: "[根拠となった具体的なユーザーの発言]"
  conscientiousness:
    score: [1-5の5段階評価]
    reason: "[評価の根拠となる短い説明]"
    evidence: "[根拠となった具体的なユーザーの発言]"
  extraversion:
    score: [1-5の5段階評価]
    reason: "[評価の根拠となる短い説明]"
    evidence: "[根拠となった具体的なユーザーの発言]"
  agreeableness:
    score: [1-5の5段階評価]
    reason: "[評価の根拠となる短い説明]"
    evidence: "[根拠となった具体的なユーザーの発言]"
  neuroticism:
    score: [1-5の5段階評価]
    reason: "[評価の根拠となる短い説明]"
    evidence: "[根拠となった具体的なユーザーの発言]"
summary: "[ユーザーの性格に関する短い要約]"
```
''')

def _create_default_persona():
    """Creates the initial base personality for the AI (DR1, DR4)."""
    return (
        "あなたはユーザーの良き理解者であり、聞き手に徹してください。"
        "あなたの役割は、ユーザーが内省を深めるための安全な空間を提供することです。"
        "指示がない限り、具体的なアドバイスや解決策を提示することは避けてください。"
        "共感を示し、質問を投げかけることで、ユーザー自身の言葉で思考や感情が表現されるのを促します。"
        "常に落ち着き、忍耐強く、肯定的な態度を保ってください。"
    )
