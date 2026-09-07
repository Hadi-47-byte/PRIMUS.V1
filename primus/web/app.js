/**
 * PRIMUS V1 — Browser Test UI
 * Minimal chat interface for the /chat API.
 */

const API_BASE = window.location.origin;
const SESSION_ID = 'primus-' + Math.random().toString(36).slice(2, 10);

const chatMessages = document.getElementById('chat-messages');
const chatForm     = document.getElementById('chat-form');
const messageInput = document.getElementById('message-input');
const sendBtn      = document.getElementById('send-btn');
const errorBanner  = document.getElementById('error-banner');
const sessionIdEl  = document.getElementById('session-id');

sessionIdEl.textContent = SESSION_ID;

/* ---- form submission ---- */

chatForm.addEventListener('submit', (e) => {
    e.preventDefault();
    sendMessage();
});

messageInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

/* ---- core send ---- */

async function sendMessage() {
    const text = messageInput.value.trim();
    if (!text) return;
    if (sendBtn.disabled) return;

    hideError();
    addMessage('user', text);
    messageInput.value = '';
    messageInput.style.height = 'auto';
    setLoading(true);

    try {
        const res = await fetch(`${API_BASE}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: SESSION_ID,
                message: text,
            }),
        });

        if (res.status === 503) {
            addMessage('error', 'PRIMUS AI service is temporarily unavailable. Please try again later.');
            return;
        }

        if (res.status === 500) {
            addMessage('error', 'An unexpected error occurred. Please try again.');
            return;
        }

        if (res.status === 400) {
            const data = await res.json();
            addMessage('error', data.detail || 'Invalid request.');
            return;
        }

        if (!res.ok) {
            addMessage('error', `Unexpected response (${res.status}).`);
            return;
        }

        const data = await res.json();
        addMessage('assistant', data.response_text, {
            state: data.interaction_state,
            topic: data.topic,
            language: data.language,
        });

    } catch (err) {
        addMessage('error', 'Cannot connect to PRIMUS. Is the server running?');
    } finally {
        setLoading(false);
    }
}

/* ---- UI helpers ---- */

function addMessage(role, text, meta) {
    const div = document.createElement('div');
    div.className = `message ${role}`;

    if (role === 'assistant' && meta) {
        const tag = document.createElement('div');
        tag.className = 'meta';
        const parts = [];
        if (meta.state) parts.push(meta.state);
        if (meta.topic) parts.push(meta.topic);
        if (meta.language) parts.push(meta.language);
        tag.textContent = parts.join(' · ');
        div.appendChild(tag);
    }

    const p = document.createElement('p');
    p.textContent = text;
    div.appendChild(p);

    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function setLoading(loading) {
    sendBtn.disabled = loading;
    messageInput.disabled = loading;
    sendBtn.textContent = loading ? '…' : 'Send';
    if (!loading) messageInput.focus();
}

function hideError() {
    errorBanner.classList.add('hidden');
    errorBanner.textContent = '';
}
