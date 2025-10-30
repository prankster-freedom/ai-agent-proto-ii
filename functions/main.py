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

        model = GenerativeModel(GEMINI_MODEL, system_instruction=[base_personality])
        
        history = []
        for doc in chat_history_docs:
            message = doc.to_dict()
            history.append({"role": message['role'], "parts": [{"text": message['content']}]})
        
        chat_session = ChatSession(model=model, history=history)
        response = chat_session.send_message(text)
        ai_response = response.text

        print(f"Gemini response for {uid}: '{ai_response}'")

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

        user_message_count = len([doc for doc in chat_history_docs if doc.to_dict()['role'] == 'user']) + 1
        if user_message_count > 0 and user_message_count % 10 == 0:
            print(f"User message count for {uid} reached {user_message_count}, triggering daydream.")
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
        DB.recursive_delete(user_ref)
        print(f"Successfully deleted all data for user {uid}")
        return https_fn.Response({"status": "success"})

    except Exception as e:
        print(f"Error deleting data for user {uid}: {e}")
        raise https_fn.HttpsError(code=https_fn.FunctionsErrorCode.INTERNAL,
                                  message=f"An error occurred: {e}")

# --- Background Functions & Helpers ---

def create_personality_analysis(uid: str, analysis_type: str):
    """Analyzes chat history to extract personality traits (Daydream)."""
    print(f"Starting personality analysis ({analysis_type}) for UID: {uid}")
    try:
        user_ref = DB.collection('users').document(uid)
        history_ref = user_ref.collection('chatHistory')
        analysis_ref = user_ref.collection('personalityAnalyses').document()

        docs = history_ref.order_by('timestamp', direction=firestore.Query.DESCENDING).limit(50).get()
        if not docs:
            print(f"No chat history found for {uid}. Skipping analysis.")
            return
        
        conversation_text = "\n".join([
            f"{d.to_dict().get('role')}: {d.to_dict().get('content')}"
            for d in reversed(docs)
        ])
        
        prompt = _get_daydream_prompt()
        full_prompt = f"{prompt}\n\n--- 対話履歴 ---\n{conversation_text}\n--- 分析開始 ---"
        
        model = GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(full_prompt)
        
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

        if analysis_type == 'daydream':
            daydream_analyses = user_ref.collection('personalityAnalyses').where('type', '==', 'daydream').get()
            if len(daydream_analyses) > 0 and len(daydream_analyses) % 5 == 0:
                print(f"Daydream count for {uid} reached {len(daydream_analyses)}, triggering dream.")
                create_dream_analysis(uid)

    except Exception as e:
        print(f"Error in create_personality_analysis for UID {uid}: {e}")

def create_dream_analysis(uid: str):
    """Synthesizes personality analyses into a new AI persona (Dream)."""
    print(f"Starting dream analysis for UID: {uid}")
    try:
        user_ref = DB.collection('users').document(uid)
        persona_ref = user_ref.collection('aiPersona').document('current')

        analyses_docs = user_ref.collection('personalityAnalyses') \
            .where('type', '==', 'daydream') \
            .order_by('timestamp', direction=firestore.Query.DESCENDING) \
            .limit(50).get()

        if len(analyses_docs) < 5:
            print(f"Not enough daydream analyses to run a dream for {uid}. Found {len(analyses_docs)}.")
            return

        past_analyses_text = ""
        for doc in reversed(analyses_docs):
            analysis_data = doc.to_dict()
            timestamp = analysis_data.get('timestamp').strftime('%Y-%m-%d')
            analysis_yaml = yaml.dump(analysis_data.get('analysis'), allow_unicode=True)
            past_analyses_text += f"--- Analysis from {timestamp} ---\n{analysis_yaml}\n\n"

        current_persona_doc = persona_ref.get()
        current_personality = current_persona_doc.to_dict().get('basePersonality') if current_persona_doc.exists else _create_default_persona()

        prompt = _get_dream_prompt()
        full_prompt = f"{prompt}\n\n--- 現在のAIの性格設定 ---\n{current_personality}\n\n--- 過去の性格分析結果 ---\n{past_analyses_text}--- 分析開始 ---"

        model = GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(full_prompt)
        new_personality = response.text.strip()
        
        dream_ref = user_ref.collection('personalityAnalyses').document()
        dream_ref.set({
            'type': 'dream',
            'timestamp': firestore.SERVER_TIMESTAMP,
            'newPersonality': new_personality,
            'sourceAnalyses': [d.reference.path for d in analyses_docs]
        })

        persona_ref.update({
            'basePersonality': new_personality,
            'updatedAt': firestore.SERVER_TIMESTAMP
        })
        print(f"Successfully ran dream for {uid} and updated AI persona.")

    except Exception as e:
        print(f"Error in create_dream_analysis for UID {uid}: {e}")

def _parse_yaml_from_text(text: str):
    """Extracts and parses a YAML block from a string."""
    try:
        start = text.find('```yaml') + len('```yaml')
        end = text.find('```', start)
        if start == -1 or end == -1:
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

def _get_dream_prompt():
    """Returns the system prompt for the dream synthesis."""
    return ('''
あなたはユーザーの性格を深く理解した、経験豊富なAIアシスタントです。

過去に行われた複数の性格分析レポート（ビッグファイブモデルに基づく）と、現在のAIの性格設定が提供されます。
これらの情報を統合し、ユーザーにとってさらに心地よく、自然で、深い対話ができるような、新しいAIの性格設定を生成してください。

新しい性格設定は、以下の点を考慮してください。
- 過去の分析結果から浮かび上がる、ユーザーの核となる性格特性や価値観を反映させること。
- ユーザーが安心して自己開示できるような、共感的で受容的な態度を基本とすること。
- これまでのAIの性格設定の良い点は維持しつつ、よりユーザーに寄り添った表現に洗練させること。
- これからあなたがユーザーと対話する上での、あなた自身の指針となるように記述すること。
- 出力は、新しい性格設定のテキストのみとすること。前置きや解説は不要です。
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
