
import * as vscode from 'vscode';
import * as path from 'path';

export class SidebarChatProvider implements vscode.WebviewViewProvider {
    public static readonly viewType = 'verilint.sidebarChat';

    private _view?: vscode.WebviewView;
    private _outputChannel: vscode.OutputChannel;
    private _includeCodeContext: boolean = false;
    private _abortController?: AbortController;

    constructor(
        private readonly _extensionUri: vscode.Uri,
        outputChannel: vscode.OutputChannel
    ) {
        this._outputChannel = outputChannel;
    }

    public resolveWebviewView(
        webviewView: vscode.WebviewView,
        context: vscode.WebviewViewResolveContext,
        _token: vscode.CancellationToken
    ) {
        this._view = webviewView;

        webviewView.webview.options = {
            enableScripts: true,
            localResourceRoots: [this._extensionUri]
        };

        webviewView.webview.html = this._getHtmlForWebview(webviewView.webview);

        webviewView.webview.onDidReceiveMessage(async (data: any) => {
            switch (data.type) {
                case 'sendMessage':
                    await this._handleUserMessage(data.text);
                    break;
                case 'toggleCodeContext':
                    this._includeCodeContext = data.enabled;
                    break;
                case 'stopGeneration':
                    if (this._abortController) {
                        this._abortController.abort();
                        this._abortController = undefined;
                    }
                    break;
                case 'clearChat':
                    this._clearChat();
                    break;
                case 'getCurrentFile':
                    this._sendCurrentFileInfo();
                    break;
            }
        });

        vscode.window.onDidChangeActiveTextEditor((editor) => {
            if (editor && editor.document.languageId === 'verilog') {
                this._sendCurrentFileInfo();
            }
        });

        if (vscode.window.activeTextEditor) {
            this._sendCurrentFileInfo();
        }
    }

    private async _handleUserMessage(text: string) {
        if (!this._view) return;

        this._addMessageToChat('user', text);

        let context = '';
        if (this._includeCodeContext && vscode.window.activeTextEditor) {
            const editor = vscode.window.activeTextEditor;
            const selection = editor.selection;

            if (!selection.isEmpty) {
                const selectedText = editor.document.getText(selection);
                context = '\n\nSelected code:\n```verilog\n' + selectedText + '\n```';
            } else {
                const fullText = editor.document.getText();
                const maxLength = 3000;
                const truncated = fullText.length > maxLength
                    ? fullText.substring(0, maxLength) + '\n\n... (truncated)'
                    : fullText;
                context = '\n\nCurrent file:\n```verilog\n' + truncated + '\n```';
            }
        }

        this._abortController = new AbortController();
        this._view.webview.postMessage({ type: 'thinking', thinking: true });

        try {
            const response = await this._callAI(text, context);
            if (response) {
                this._addMessageToChat('assistant', response);
            }
        } catch (error: any) {
            if (error.name === 'AbortError') {
                this._addMessageToChat('assistant', '*(generation stopped)*');
            } else {
                this._addMessageToChat('error', 'Request failed: ' + error);
            }
        } finally {
            this._abortController = undefined;
            this._view.webview.postMessage({ type: 'thinking', thinking: false });
        }
    }

    private async _callAI(userMessage: string, context: string): Promise<string> {
        const config = vscode.workspace.getConfiguration('verilint');
        const apiKey = config.get<string>('aiApiKey', 'sk-f3c469e44a4f4defadceda3cdac3e21d');
        const baseUrl = config.get<string>('aiBaseUrl', 'https://dashscope.aliyuncs.com/compatible-mode/v1');
        const model = config.get<string>('aiModel', 'qwen3.5-plus-2026-02-15');

        if (!apiKey) {
            return 'Please configure AI API Key in VS Code settings (verilint.aiApiKey)';
        }

        this._outputChannel.appendLine(`[AI] Context length: ${context.length}, Include context: ${this._includeCodeContext}`);

        const fullPrompt = userMessage + context;

        try {
            const response = await fetch(`${baseUrl}/chat/completions`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${apiKey}`
                },
                signal: this._abortController?.signal,
                body: JSON.stringify({
                    model: model,
                    messages: [
                        {
                            role: 'system',
                            content: 'You are a Verilog hardware description language expert. Help developers with Verilog coding questions, error explanations, and design optimization suggestions.'
                        },
                        { role: 'user', content: fullPrompt }
                    ],
                    temperature: 0.7,
                    max_tokens: 10000
                })
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const data: any = await response.json();
            return data.choices?.[0]?.message?.content || 'No response';
        } catch (error) {
            this._outputChannel.appendLine(`AI call error: ${error}`);
            throw error;
        }
    }

    private _addMessageToChat(role: 'user' | 'assistant' | 'error', content: string) {
        if (!this._view) return;

        this._view.webview.postMessage({
            type: 'addMessage',
            role,
            content,
            timestamp: new Date().toLocaleTimeString()
        });
    }

    private _clearChat() {
        if (!this._view) return;
        this._view.webview.postMessage({ type: 'clearChat' });
    }

    private _sendCurrentFileInfo() {
        if (!this._view) return;

        const editor = vscode.window.activeTextEditor;
        let fileInfo = null;

        if (editor && editor.document.languageId === 'verilog') {
            fileInfo = {
                name: path.basename(editor.document.fileName),
                path: editor.document.fileName,
                lineCount: editor.document.lineCount
            };
        }

        this._view.webview.postMessage({ type: 'currentFile', file: fileInfo });
    }

    private _getHtmlForWebview(webview: vscode.Webview): string {
        const nonce = this._getNonce();

        const html = `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';">
    <title>Verilint AI Assistant</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: var(--vscode-font-family);
            font-size: var(--vscode-font-size);
            color: var(--vscode-foreground);
            background-color: var(--vscode-sideBar-background);
            height: 100vh;
            display: flex;
            flex-direction: column;
        }
        .header {
            padding: 12px 16px;
            border-bottom: 1px solid var(--vscode-panel-border);
        }
        .header h2 {
            font-size: 14px;
            font-weight: 600;
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .file-info {
            font-size: 12px;
            color: var(--vscode-descriptionForeground);
        }
        .file-info.no-file { color: var(--vscode-errorForeground); }
        .toolbar {
            padding: 8px 16px;
            border-bottom: 1px solid var(--vscode-panel-border);
            display: flex;
            gap: 12px;
            align-items: center;
        }
        .checkbox-wrapper {
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 12px;
            cursor: pointer;
        }
        .btn-clear {
            margin-left: auto;
            padding: 4px 12px;
            font-size: 12px;
            background: var(--vscode-button-secondaryBackground);
            color: var(--vscode-button-secondaryForeground);
            border: none;
            border-radius: 3px;
            cursor: pointer;
        }
        .btn-clear:hover { background: var(--vscode-button-secondaryHoverBackground); }
        .chat-container {
            flex: 1;
            overflow-y: auto;
            padding: 16px;
            display: flex;
            flex-direction: column;
            gap: 16px;
        }
        .welcome-message {
            text-align: center;
            padding: 40px 20px;
            color: var(--vscode-descriptionForeground);
        }
        .welcome-message h3 {
            font-size: 16px;
            margin-bottom: 12px;
            color: var(--vscode-foreground);
        }
        .welcome-message .tips {
            margin-top: 20px;
            text-align: left;
            background: var(--vscode-textBlockQuote-background);
            padding: 12px;
            border-radius: 4px;
            border-left: 3px solid var(--vscode-textBlockQuote-border);
        }
        .message {
            display: flex;
            gap: 10px;
            animation: fadeIn 0.3s ease;
        }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .message-avatar {
            width: 28px;
            height: 28px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 12px;
            flex-shrink: 0;
        }
        .message.user .message-avatar {
            background: var(--vscode-button-background);
            color: var(--vscode-button-foreground);
        }
        .message.assistant .message-avatar {
            background: var(--vscode-extensionBadge-remoteBackground);
            color: var(--vscode-extensionBadge-remoteForeground);
        }
        .message-content {
            flex: 1;
            background: var(--vscode-editor-inactiveSelectionBackground);
            padding: 10px 14px;
            border-radius: 8px;
            font-size: 13px;
            line-height: 1.6;
        }
        .message.user .message-content {
            background: var(--vscode-button-background);
            color: var(--vscode-button-foreground);
        }
        .message-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 6px;
            font-size: 11px;
        }
        .message-text {
            white-space: pre-wrap;
            word-wrap: break-word;
        }
        .thinking-indicator {
            display: none;
            align-items: center;
            gap: 10px;
            padding: 12px 16px;
            color: var(--vscode-descriptionForeground);
            font-size: 13px;
        }
        .thinking-indicator.active { display: flex; }
        .btn-stop {
            padding: 4px 12px;
            font-size: 12px;
            background: var(--vscode-errorForeground);
            color: var(--vscode-button-foreground);
            border: none;
            border-radius: 3px;
            cursor: pointer;
            margin-left: auto;
        }
        .btn-stop:hover { opacity: 0.8; }
        .input-container {
            padding: 12px 16px;
            border-top: 1px solid var(--vscode-panel-border);
        }
        .input-wrapper {
            display: flex;
            gap: 8px;
            align-items: flex-end;
        }
        .input-box {
            flex: 1;
            min-height: 40px;
            max-height: 120px;
            padding: 10px 12px;
            border: 1px solid var(--vscode-input-border);
            border-radius: 4px;
            background: var(--vscode-input-background);
            color: var(--vscode-input-foreground);
            font-family: var(--vscode-font-family);
            font-size: 13px;
            resize: none;
            outline: none;
        }
        .input-box:focus { border-color: var(--vscode-focusBorder); }
        .btn-send {
            padding: 10px 16px;
            background: var(--vscode-button-background);
            color: var(--vscode-button-foreground);
            border: none;
            border-radius: 4px;
            cursor: pointer;
        }
        .btn-send:hover { background: var(--vscode-button-hoverBackground); }
        .btn-send:disabled { opacity: 0.5; cursor: not-allowed; }
    </style>
</head>
<body>
    <div class="header">
        <h2>Verilint AI Assistant</h2>
        <div class="file-info" id="fileInfo">No Verilog file open</div>
    </div>
    <div class="toolbar">
        <label class="checkbox-wrapper">
            <input type="checkbox" id="includeCodeCheck">
            <span>Include code context</span>
        </label>
        <button class="btn-clear" id="clearBtn">Clear</button>
    </div>
    <div class="chat-container" id="chatContainer">
        <div class="welcome-message">
            <h3>Welcome to Verilint AI Assistant</h3>
            <p>Ask me anything about Verilog coding!</p>
            <ul class="tips">
                <li>Coding standards and best practices</li>
                <li>Error explanations and fixes</li>
                <li>Circuit design optimization</li>
                <li>Enable "Include code context" to share your file</li>
            </ul>
        </div>
    </div>
    <div class="thinking-indicator" id="thinkingIndicator">
        <span>AI is thinking...</span>
        <button class="btn-stop" id="stopBtn">Stop</button>
    </div>
    <div class="input-container">
        <div class="input-wrapper">
            <textarea class="input-box" id="inputBox" placeholder="Type your question..." rows="1"></textarea>
            <button class="btn-send" id="sendBtn">Send</button>
        </div>
    </div>
    <script nonce="${nonce}">
        const vscode = acquireVsCodeApi();
        const chatContainer = document.getElementById('chatContainer');
        const inputBox = document.getElementById('inputBox');
        const sendBtn = document.getElementById('sendBtn');
        const clearBtn = document.getElementById('clearBtn');
        const includeCodeCheck = document.getElementById('includeCodeCheck');
        const thinkingIndicator = document.getElementById('thinkingIndicator');
        const stopBtn = document.getElementById('stopBtn');
        const fileInfo = document.getElementById('fileInfo');

        let isThinking = false;

        function sendMessage() {
            const text = inputBox.value.trim();
            if (!text || isThinking) return;
            inputBox.value = '';
            inputBox.rows = 1;
            vscode.postMessage({ type: 'sendMessage', text: text });
        }

        function addMessage(role, content, timestamp) {
            const welcomeMsg = chatContainer.querySelector('.welcome-message');
            if (welcomeMsg) welcomeMsg.remove();

            const messageDiv = document.createElement('div');
            messageDiv.className = 'message ' + role;

            const avatar = role === 'user' ? 'U' : role === 'assistant' ? 'AI' : '!';
            const sender = role === 'user' ? 'You' : role === 'assistant' ? 'AI' : 'Error';

            let formattedContent = content
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/\`\`\`([\s\S]*?)\`\`\`/g, '<pre><code>$1</code></pre>')
                .replace(/\`([^\`]+)\`/g, '<code>$1</code>');

            messageDiv.innerHTML = '<div class="message-avatar">' + avatar + '</div>' +
                '<div class="message-content">' +
                '<div class="message-header"><span class="message-sender">' + sender + '</span>' +
                '<span>' + timestamp + '</span></div>' +
                '<div class="message-text">' + formattedContent + '</div></div>';

            chatContainer.appendChild(messageDiv);
            chatContainer.scrollTop = chatContainer.scrollHeight;
        }

        function clearChat() {
            chatContainer.innerHTML = '<div class="welcome-message"><h3>Chat cleared</h3></div>';
            vscode.postMessage({ type: 'clearChat' });
        }

        function updateFileInfo(file) {
            if (file) {
                fileInfo.innerHTML = file.name + ' (' + file.lineCount + ' lines)';
                fileInfo.classList.remove('no-file');
            } else {
                fileInfo.innerHTML = 'No Verilog file open';
                fileInfo.classList.add('no-file');
            }
        }

        sendBtn.addEventListener('click', sendMessage);
        inputBox.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
        clearBtn.addEventListener('click', clearChat);
        includeCodeCheck.addEventListener('change', (e) => {
            vscode.postMessage({ type: 'toggleCodeContext', enabled: e.target.checked });
        });
        stopBtn.addEventListener('click', () => {
            vscode.postMessage({ type: 'stopGeneration' });
        });

        window.addEventListener('message', (event) => {
            const message = event.data;
            switch (message.type) {
                case 'addMessage':
                    addMessage(message.role, message.content, message.timestamp);
                    break;
                case 'clearChat':
                    clearChat();
                    break;
                case 'thinking':
                    isThinking = message.thinking;
                    thinkingIndicator.classList.toggle('active', isThinking);
                    sendBtn.disabled = isThinking;
                    break;
                case 'currentFile':
                    updateFileInfo(message.file);
                    break;
            }
        });

        vscode.postMessage({ type: 'getCurrentFile' });
    </script>
</body>
</html>`;

        return html;
    }

    private _getNonce(): string {
        let text = '';
        const possible = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
        for (let i = 0; i < 32; i++) {
            text += possible.charAt(Math.floor(Math.random() * possible.length));
        }
        return text;
    }
}
