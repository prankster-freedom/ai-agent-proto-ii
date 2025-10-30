# Firebase Deployment Guide

This guide provides instructions on how to deploy this application to Firebase using the standard Firebase Command Line Interface (CLI).

---

## 1. Understanding the Architecture

This application consists of two main parts:

1.  **Frontend (Firebase Hosting):** All the static web files (HTML, CSS, JavaScript) are located in the `static` directory. These are served to users via Firebase Hosting.
2.  **Backend (Cloud Functions for Firebase):** The backend logic, including the chat endpoint and the personality analysis functions (`daydream`, `dream`), is written in Python and located in the `functions` directory. These are deployed as Cloud Functions.

The Firebase CLI can deploy both parts simultaneously.

## 2. Prerequisites

Before deploying, ensure you have the following set up:

1.  **Node.js and npm:** [Install Node.js](https://nodejs.org/), which includes npm.
2.  **Firebase CLI:** Install the Firebase CLI globally and log in.
    ```bash
    npm install -g firebase-tools
    firebase login
    ```
3.  **Firebase Project:** Create a project in the [Firebase Console](https://console.firebase.google.com/).
4.  **Blaze Plan:** Enable the **Blaze (Pay-as-you-go)** billing plan for your project. This is required to use the Gemini API and to deploy Cloud Functions.
5.  **Project Association:** Associate your local project directory with your Firebase project.
    ```bash
    firebase use --add
    ```
    Select your project from the list.

## 3. Configuration (`firebase.json`)

The `firebase.json` file in the root of the project tells the CLI how to deploy your app. It should look like this:

```json
{
  "functions": {
    "source": "functions"
  },
  "hosting": {
    "public": "static",
    "ignore": [
      "firebase.json",
      "**/.*",
      "**/node_modules/**"
    ],
    "rewrites": [
      {
        "source": "**",
        "destination": "/index.html"
      }
    ]
  },
  "emulators": {
    "auth": {
      "port": 9099
    },
    "functions": {
      "port": 5001
    },
    "firestore": {
      "port": 8080
    },
    "hosting": {
      "port": 5000
    },
    "ui": {
      "enabled": true,
      "port": 4000
    }
  }
}
```

- `functions.source`: Specifies that your Cloud Functions code is in the `functions` directory.
- `hosting.public`: Specifies that the `static` directory contains the files to be deployed to Firebase Hosting.

## 4. Environment Variables & Best Practices

Your backend code in `functions/main.py` needs to know your Google Cloud Project ID to initialize the Vertex AI client for the Gemini API.

Currently, it is hardcoded:
`PROJECT_ID = "gma-stg"`

When you deploy a function, Cloud Functions for Firebase automatically provides the project ID in an environment variable (`GCLOUD_PROJECT`). It is a best practice to use this variable instead of hardcoding the ID.

**Recommendation:** Before deploying, modify `functions/main.py` to get the Project ID dynamically.

```python
# functions/main.py

import os # <-- Add this import

# ...

# Initialize Vertex AI
# PROJECT_ID = "gma-stg" # <-- Comment out or delete this line
PROJECT_ID = os.environ.get('GCLOUD_PROJECT', 'gma-stg') # <-- Add this line
LOCATION = "asia-northeast1"
vertexai.init(project=PROJECT_ID, location=LOCATION)

# ...
```
This change makes your code more portable and secure. It will use the correct project ID when deployed, and fall back to `gma-stg` when running in an environment where the variable isn't set (like the local emulator).

## 5. Deploying the Application

Once the prerequisites and configuration are in place, deploy your entire application with a single command from the root of your project directory:

```bash
firebase deploy
```

This command will:
1.  Package and deploy the Python functions from the `functions` directory.
2.  Upload and deploy the web content from the `static` directory to Firebase Hosting.

After the command completes, it will output the URL where your application is live. You can visit this URL to use your fully deployed application.
