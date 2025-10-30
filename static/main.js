
// Firebase configuration
const firebaseConfig = {
    apiKey: "your-api-key", // Replace with your actual config
    authDomain: "your-auth-domain",
    projectId: "your-project-id",
    storageBucket: "your-storage-bucket",
    messagingSenderId: "your-messaging-sender-id",
    appId: "your-app-id"
};

// Initialize Firebase
const app = firebase.initializeApp(firebaseConfig);
const auth = firebase.auth();
const firestore = firebase.firestore();
const functions = firebase.functions();

// Use emulators if running locally
if (window.location.hostname === 'localhost') {
    console.log("Running in emulator mode");
    auth.useEmulator('http://localhost:9099');
    firestore.useEmulator('localhost', 8080);
    functions.useEmulator('localhost', 5001);
}

// DOM Elements
const signInButton = document.getElementById('sign-in');
const signOutButton = document.getElementById('sign-out');
const chatForm = document.getElementById('chat-form');
const chatInput = document.getElementById('chat-input');
const deleteMemoryButton = document.getElementById('delete-memory');
const userInfo = document.getElementById('user-info');
const userName = document.getElementById('user-name');
const authContainer = document.getElementById('auth-container');
const chatContainer = document.getElementById('chat-container');
const footerContainer = document.getElementById('footer-container');
const chatHistory = document.getElementById('chat-history');

let unsubscribeChatHistory = null;

// --- Authentication ---
const provider = new firebase.auth.GoogleAuthProvider();

signInButton.onclick = () => auth.signInWithPopup(provider);

signOutButton.onclick = () => {
    if (unsubscribeChatHistory) {
        unsubscribeChatHistory();
    }
    auth.signOut();
};

auth.onAuthStateChanged(user => {
    if (user) {
        // User is signed in.
        userName.textContent = user.displayName;
        authContainer.style.display = 'none';
        userInfo.style.display = 'block';
        chatContainer.style.display = 'block';
        footerContainer.style.display = 'block';

        listenToChatHistory(user.uid);
        console.log("User signed in:", user.uid);

    } else {
        // User is signed out.
        authContainer.style.display = 'block';
        userInfo.style.display = 'none';
        chatContainer.style.display = 'none';
        footerContainer.style.display = 'none';
        if (unsubscribeChatHistory) {
            unsubscribeChatHistory();
        }
        chatHistory.innerHTML = ''; // Clear chat history on sign out
        console.log("User signed out");
    }
});

// --- Chat ---

function listenToChatHistory(uid) {
    const query = firestore.collection('users').doc(uid).collection('chatHistory').orderBy('timestamp');

    unsubscribeChatHistory = query.onSnapshot(snapshot => {
        chatHistory.innerHTML = ''; // Clear existing messages
        snapshot.forEach(doc => {
            const message = doc.data();
            appendMessage(message.content, message.role);
        });
        scrollToBottom();
    }, err => {
        console.error("Error listening to chat history:", err);
    });
}

function appendMessage(text, role) {
    const messageElement = document.createElement('div');
    messageElement.classList.add('message', `${role}-message`);
    messageElement.textContent = text;
    chatHistory.appendChild(messageElement);
}

function scrollToBottom() {
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

chatForm.onsubmit = async (e) => {
    e.preventDefault();
    const message = chatInput.value;
    chatInput.value = '';

    if (!message.trim()) {
        console.log("Input is empty, not sending.");
        return;
    }

    appendMessage(message, 'user');
    scrollToBottom();

    try {
        const chatFunction = functions.httpsCallable('chat');
        const result = await chatFunction({ text: message });
        // The AI response will be added by the onSnapshot listener
        console.log("Received response from backend:", result.data);

    } catch (error) {
        console.error("Error calling chat function:", error);
        appendMessage("Error: Could not get a response.", 'model-error');
        scrollToBottom();
    }
};


// --- Memory Deletion ---
deleteMemoryButton.onclick = async () => {

    if (!confirm("Are you sure you want to delete all your memories? This action cannot be undone.")) {
        return;
    }

    console.log("Requesting memory deletion...");

    try {
        const deleteMemoryFunction = functions.httpsCallable('deleteMemory');
        await deleteMemoryFunction();
        console.log("Memory deletion successful.");
        // Chat history is cleared by the onSnapshot listener reacting to the deletion
        alert("Your memory has been deleted.");

    } catch (error) {
        console.error("Error calling deleteMemory function:", error);
        alert("Failed to delete your memory.");
    }
};

// --- FR2: Idle Timer ---
let idleTimer;
const IDLE_TIMEOUT = 3 * 60 * 1000; // 3 minutes

function resetIdleTimer() {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
        console.log("User idle, asking a question.");
        // TODO: Call backend to get a proactive question
    }, IDLE_TIMEOUT);
}

// Reset timer on user activity
window.onload = resetIdleTimer;
document.onmousemove = resetIdleTimer;
document.onkeypress = resetIdleTimer;
chatInput.onfocus = resetIdleTimer;
