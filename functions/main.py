# -*- coding: utf-8 -*-

# Firebase SDK
import firebase_admin
from firebase_admin import credentials, firestore, functions
from firebase_functions import https_fn, options

# Initialize Firebase Admin SDK
# credentials.ApplicationDefault() is used to automatically find the credentials
# during initialization. This works in most GCP environments.
firebase_admin.initialize_app()

# Set CORS options for local development with Firebase Emulator Suite
# This is necessary for the callable function to be accessible from the web client
# running on the hosting emulator.
options.set_global_options(
    cors=options.CorsOptions(
        cors_origins=[
            "http://localhost:5000", 
            "http://127.0.0.1:5000", 
            "your-production-site-url" # TODO: Replace with your actual production site URL
        ],
        cors_methods=["GET", "POST"],
    )
)

@https_fn.on_call()
def chat(req: https_fn.CallableRequest) -> https_fn.Response:
    """Handles a chat message from the user."""
    print(f"Received chat request from UID: {req.auth.uid}")
    print(f"Request data: {req.data}")

    # TODO: Validate input
    text = req.data.get("text", "")

    # TODO: Get chat history and AI persona from Firestore

    # TODO: Call Gemini API
    ai_response = f"Echo from backend: {text}"

    # TODO: Save user message and AI response to Firestore
    
    # TODO: Check for daydream trigger (10th message)

    return https_fn.Response({"text": ai_response})

@https_fn.on_call()
def deleteMemory(req: https_fn.CallableRequest) -> https_fn.Response:
    """Deletes all data for the requesting user."""
    if not req.auth:
        raise https_fn.HttpsError(
            code=https_fn.FunctionsErrorCode.UNAUTHENTICATED,
            message="The function must be called while authenticated.",
        )

    uid = req.auth.uid
    print(f"Received memory deletion request for UID: {uid}")

    try:
        db = firestore.client()
        user_ref = db.collection('users').document(uid)
        
        # This is a recursive delete, it will delete all subcollections.
        # Be careful with this in production.
        db.recursive_delete(user_ref)
        
        print(f"Successfully deleted all data for user {uid}")
        return https_fn.Response({"status": "success"})

    except Exception as e:
        print(f"Error deleting data for user {uid}: {e}")
        raise https_fn.HttpsError(
            code=https_fn.FunctionsErrorCode.INTERNAL,
            message=f"An error occurred while deleting user data: {e}",
        )
