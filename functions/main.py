# -*- coding: utf-8 -*-

import firebase_admin
from firebase_admin import credentials, firestore, functions
from firebase_functions import https_fn, options
import vertexai
from vertexai.generative_models import GenerativeModel, ChatSession
import yaml

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
            "gma-stg.web.app" # Production site URL
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
        
        # Reconstruct chat history for the model
        history = []
        for doc in chat_history_docs:
            message = doc.to_dict()
            # Convert role for Gemini API if necessary, assuming 'user' and 'model' match
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
        if user_message_count % 10 == 0:
            print(f"User message count for {uid} reached {user_message_count}, triggering daydream.")
            # This will be an asynchronous call, no need to wait.
            # We will implement this function in the next step.
            # create_personality_analysis(uid, 'daydream') 

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

# --- Helper Functions ---

def _create_default_persona():
    """Creates the initial base personality for the AI (DR1, DR4)."""
    return (
        "あなたはユーザーの良き理解者であり、聞き手に徹してください。"
        "あなたの役割は、ユーザーが内省を深めるための安全な空間を提供することです。"
        "指示がない限り、具体的なアドバイスや解決策を提示することは避けてください。"
        "共感を示し、質問を投げかけることで、ユーザー自身の言葉で思考や感情が表現されるのを促します。"
        "常に落ち着き、忍耐強く、肯定的な態度を保ってください。"
    )

