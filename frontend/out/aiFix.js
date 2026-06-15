"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || function (mod) {
    if (mod && mod.__esModule) return mod;
    var result = {};
    if (mod != null) for (var k in mod) if (k !== "default" && Object.prototype.hasOwnProperty.call(mod, k)) __createBinding(result, mod, k);
    __setModuleDefault(result, mod);
    return result;
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.AIFixProvider = void 0;
const vscode = __importStar(require("vscode"));
const child_process_1 = require("child_process");
const path = __importStar(require("path"));
const fs = __importStar(require("fs"));
class AIFixProvider {
    constructor(outputChannel) {
        this.fixResults = new Map();
        this.outputChannel = outputChannel;
    }
    /**
     * Get AI fix for a specific issue
     */
    async getFix(document, issue) {
        const config = vscode.workspace.getConfiguration('verilint');
        const pythonPath = config.get('pythonPath', 'python');
        // Find AI fix bridge script
        let agentPath = config.get('aiAgentPath', '');
        if (!agentPath) {
            const workspaceFolders = vscode.workspace.workspaceFolders;
            if (workspaceFolders) {
                for (const folder of workspaceFolders) {
                    const possiblePaths = [
                        path.join(folder.uri.fsPath, 'ai_fix_bridge.py'),
                        path.join(folder.uri.fsPath, 'ModifiedAgent.py'),
                        path.join(folder.uri.fsPath, 'final_lint', 'ModifiedAgent.py'),
                    ];
                    for (const p of possiblePaths) {
                        if (fs.existsSync(p)) {
                            agentPath = p;
                            break;
                        }
                    }
                    if (agentPath)
                        break;
                }
            }
        }
        if (!agentPath || !fs.existsSync(agentPath)) {
            vscode.window.showErrorMessage('AI Agent not found. Please set verilint.aiAgentPath in settings.');
            return null;
        }
        // Read the document content
        const code = document.getText();
        // Prepare error information
        const errorInfo = this.formatErrorInfo(issue);
        // Create a temporary file with the request
        const tempFile = path.join(path.dirname(document.fileName), `.verilint_ai_fix_${Date.now()}.json`);
        const requestData = {
            code: code,
            error: errorInfo,
            filePath: document.fileName,
            line: issue.line,
            column: issue.column,
            message: issue.message,
            category: issue.category
        };
        fs.writeFileSync(tempFile, JSON.stringify(requestData, null, 2));
        this.outputChannel.appendLine(`Requesting AI fix for: ${issue.message} at line ${issue.line}`);
        return new Promise((resolve, reject) => {
            const process = (0, child_process_1.spawn)(pythonPath, [agentPath, tempFile]);
            let stdout = '';
            let stderr = '';
            process.stdout.on('data', (data) => {
                stdout += data.toString();
            });
            process.stderr.on('data', (data) => {
                stderr += data.toString();
            });
            process.on('close', (_code) => {
                // Clean up temp file
                try {
                    fs.unlinkSync(tempFile);
                }
                catch (e) {
                    // Ignore cleanup errors
                }
                if (stderr) {
                    this.outputChannel.appendLine(`AI Agent stderr: ${stderr}`);
                }
                try {
                    // Try to parse JSON response first
                    const result = JSON.parse(stdout);
                    this.outputChannel.appendLine(`AI fix received for line ${issue.line}`);
                    resolve(result);
                }
                catch (e) {
                    // If not JSON, treat as plain text response
                    const result = {
                        originalCode: this.getLineAt(document, issue.line),
                        fixedCode: '',
                        explanation: stdout,
                        line: issue.line,
                        column: issue.column
                    };
                    this.outputChannel.appendLine(`AI explanation received for line ${issue.line}`);
                    resolve(result);
                }
            });
            process.on('error', (err) => {
                // Clean up temp file
                try {
                    fs.unlinkSync(tempFile);
                }
                catch (e) {
                    // Ignore cleanup errors
                }
                this.outputChannel.appendLine(`Error running AI agent: ${err.message}`);
                reject(err);
            });
        });
    }
    /**
     * Format error information for the AI agent
     */
    formatErrorInfo(issue) {
        return `[${issue.severity.toUpperCase()}] Line ${issue.line}, Column ${issue.column}: ${issue.message} (${issue.code})`;
    }
    /**
     * Get the code at a specific line
     */
    getLineAt(document, line) {
        const lineIndex = line - 1; // VSCode uses 0-based indexing
        if (lineIndex >= 0 && lineIndex < document.lineCount) {
            return document.lineAt(lineIndex).text;
        }
        return '';
    }
    /**
     * Show AI fix result in a webview panel
     */
    async showFixResult(document, issue, result) {
        const panel = vscode.window.createWebviewPanel('aiFixResult', `AI Fix: ${issue.message.substring(0, 30)}...`, vscode.ViewColumn.Beside, {
            enableScripts: true,
            retainContextWhenHidden: true
        });
        panel.webview.html = this.getWebviewContent(result, issue);
        // Handle messages from the webview
        panel.webview.onDidReceiveMessage(async (message) => {
            switch (message.command) {
                case 'applyFix':
                    await this.applyFix(document, result);
                    vscode.window.showInformationMessage('Fix applied successfully');
                    panel.dispose();
                    break;
                case 'copyFix':
                    await vscode.env.clipboard.writeText(result.fixedCode);
                    vscode.window.showInformationMessage('Fix copied to clipboard');
                    break;
                case 'close':
                    panel.dispose();
                    break;
            }
        }, undefined, []);
    }
    /**
     * Apply the fix to the document
     */
    async applyFix(document, result) {
        const edit = new vscode.WorkspaceEdit();
        const lineIndex = result.line - 1;
        if (lineIndex >= 0 && lineIndex < document.lineCount) {
            const line = document.lineAt(lineIndex);
            const range = new vscode.Range(lineIndex, 0, lineIndex, line.text.length);
            edit.replace(document.uri, range, result.fixedCode);
            await vscode.workspace.applyEdit(edit);
            await document.save();
        }
    }
    /**
     * Generate HTML content for the webview
     */
    getWebviewContent(result, issue) {
        return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Fix Result</title>
    <style>
        body {
            font-family: var(--vscode-font-family);
            font-size: var(--vscode-font-size);
            color: var(--vscode-foreground);
            background-color: var(--vscode-editor-background);
            padding: 20px;
        }
        .header {
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 1px solid var(--vscode-panel-border);
        }
        .error-info {
            color: var(--vscode-errorForeground);
            margin-bottom: 10px;
        }
        .section {
            margin-bottom: 20px;
        }
        .section-title {
            font-weight: bold;
            margin-bottom: 10px;
            color: var(--vscode-descriptionForeground);
        }
        .code-block {
            background-color: var(--vscode-textCodeBlock-background);
            padding: 10px;
            border-radius: 4px;
            font-family: var(--vscode-editor-font-family);
            font-size: var(--vscode-editor-font-size);
            overflow-x: auto;
            white-space: pre-wrap;
            word-wrap: break-word;
        }
        .original-code {
            border-left: 3px solid var(--vscode-errorForeground);
        }
        .fixed-code {
            border-left: 3px solid var(--vscode-testing-iconPassed);
        }
        .button-container {
            display: flex;
            gap: 10px;
            margin-top: 20px;
        }
        button {
            padding: 8px 16px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 13px;
        }
        .apply-btn {
            background-color: var(--vscode-button-background);
            color: var(--vscode-button-foreground);
        }
        .apply-btn:hover {
            background-color: var(--vscode-button-hoverBackground);
        }
        .copy-btn {
            background-color: var(--vscode-secondaryButton-background);
            color: var(--vscode-secondaryButton-foreground);
        }
        .close-btn {
            background-color: transparent;
            color: var(--vscode-foreground);
            border: 1px solid var(--vscode-panel-border);
        }
        .explanation {
            line-height: 1.6;
        }
        .loading {
            text-align: center;
            padding: 40px;
            color: var(--vscode-descriptionForeground);
        }
    </style>
</head>
<body>
    <div class="header">
        <h2>🤖 AI Fix Suggestion</h2>
        <div class="error-info">
            <strong>Error:</strong> ${this.escapeHtml(issue.message)}<br>
            <strong>Location:</strong> Line ${issue.line}, Column ${issue.column}<br>
            <strong>Category:</strong> ${issue.category}
        </div>
    </div>

    <div class="section">
        <div class="section-title">📋 Original Code</div>
        <div class="code-block original-code">${this.escapeHtml(result.originalCode)}</div>
    </div>

    ${result.fixedCode ? `
    <div class="section">
        <div class="section-title">✅ Suggested Fix</div>
        <div class="code-block fixed-code">${this.escapeHtml(result.fixedCode)}</div>
    </div>
    ` : ''}

    <div class="section">
        <div class="section-title">💡 Explanation</div>
        <div class="explanation">${this.formatExplanation(result.explanation)}</div>
    </div>

    <div class="button-container">
        ${result.fixedCode ? `<button class="apply-btn" onclick="applyFix()">Apply Fix</button>` : ''}
        ${result.fixedCode ? `<button class="copy-btn" onclick="copyFix()">Copy to Clipboard</button>` : ''}
        <button class="close-btn" onclick="closePanel()">Close</button>
    </div>

    <script>
        const vscode = acquireVsCodeApi();

        function applyFix() {
            vscode.postMessage({ command: 'applyFix' });
        }

        function copyFix() {
            vscode.postMessage({ command: 'copyFix' });
        }

        function closePanel() {
            vscode.postMessage({ command: 'close' });
        }
    </script>
</body>
</html>`;
    }
    /**
     * Escape HTML special characters
     */
    escapeHtml(text) {
        return text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }
    /**
     * Format explanation text (convert newlines to <br>, etc.)
     */
    formatExplanation(text) {
        // Convert markdown-style code blocks to HTML
        let formatted = this.escapeHtml(text);
        formatted = formatted.replace(/```verilog\n([\s\S]*?)```/g, '<div class="code-block">$1</div>');
        formatted = formatted.replace(/```\n([\s\S]*?)```/g, '<div class="code-block">$1</div>');
        formatted = formatted.replace(/`([^`]+)`/g, '<code>$1</code>');
        formatted = formatted.replace(/\n\n/g, '</p><p>');
        formatted = formatted.replace(/\n/g, '<br>');
        return `<p>${formatted}</p>`;
    }
}
exports.AIFixProvider = AIFixProvider;
//# sourceMappingURL=aiFix.js.map