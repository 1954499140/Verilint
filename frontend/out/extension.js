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
exports.deactivate = exports.activate = void 0;
const vscode = __importStar(require("vscode"));
const linter_1 = require("./linter");
const diagnostics_1 = require("./diagnostics");
const codelens_1 = require("./codelens");
const aiFix_1 = require("./aiFix");
const sidebarChat_1 = require("./sidebarChat");
let linter;
let diagnosticsProvider;
let aiFixProvider;
let sidebarChatProvider;
let outputChannel;
function activate(context) {
    outputChannel = vscode.window.createOutputChannel('Verilint');
    outputChannel.appendLine('Verilint extension activated');
    linter = new linter_1.VerilintLinter(outputChannel);
    diagnosticsProvider = new diagnostics_1.VerilintDiagnosticsProvider(linter, outputChannel);
    aiFixProvider = new aiFix_1.AIFixProvider(outputChannel);
    sidebarChatProvider = new sidebarChat_1.SidebarChatProvider(context.extensionUri, outputChannel);
    // Register sidebar chat provider
    context.subscriptions.push(vscode.window.registerWebviewViewProvider(sidebarChat_1.SidebarChatProvider.viewType, sidebarChatProvider));
    // Register diagnostics collection
    const diagnosticCollection = vscode.languages.createDiagnosticCollection('verilint');
    context.subscriptions.push(diagnosticCollection);
    // Register code lens provider
    const codeLensProvider = new codelens_1.VerilintCodeLensProvider();
    context.subscriptions.push(vscode.languages.registerCodeLensProvider('verilog', codeLensProvider));
    // Set up diagnostics provider with code lens
    diagnosticsProvider.setDiagnosticCollection(diagnosticCollection);
    diagnosticsProvider.setCodeLensProvider(codeLensProvider);
    // Register AI Fix command for CodeLens
    context.subscriptions.push(vscode.commands.registerCommand('verilint.aiFix', async (document, issue) => {
        const editor = vscode.window.activeTextEditor;
        if (!editor || editor.document.uri.toString() !== document.uri.toString()) {
            vscode.window.showWarningMessage('Please open the file with the error first');
            return;
        }
        // Show progress
        await vscode.window.withProgress({
            location: vscode.ProgressLocation.Notification,
            title: "🤖 Getting AI fix suggestion...",
            cancellable: false
        }, async (progress) => {
            try {
                progress.report({ increment: 30, message: "Analyzing error..." });
                const result = await aiFixProvider.getFix(document, issue);
                if (result) {
                    progress.report({ increment: 70, message: "Displaying result..." });
                    await aiFixProvider.showFixResult(document, issue, result);
                }
                else {
                    vscode.window.showErrorMessage('Failed to get AI fix');
                }
            }
            catch (error) {
                vscode.window.showErrorMessage(`AI Fix error: ${error}`);
            }
        });
    }));
    // Register command to show all issues and select one to fix
    context.subscriptions.push(vscode.commands.registerCommand('verilint.showIssuesAndFix', async () => {
        const editor = vscode.window.activeTextEditor;
        if (!editor || editor.document.languageId !== 'verilog') {
            vscode.window.showWarningMessage('No Verilog file is currently open');
            return;
        }
        const diagnostics = diagnosticCollection.get(editor.document.uri);
        if (!diagnostics || diagnostics.length === 0) {
            vscode.window.showInformationMessage('No issues found in current file');
            return;
        }
        // Create quick pick items for each diagnostic
        const items = diagnostics.map((d, index) => ({
            label: `${d.code}: ${d.message.substring(0, 50)}...`,
            description: `Line ${d.range.start.line + 1}`,
            detail: d.message,
            index: index,
            diagnostic: d
        }));
        const selected = await vscode.window.showQuickPick(items, {
            placeHolder: 'Select an issue to get AI fix',
            title: 'Select Issue for AI Fix'
        });
        if (selected) {
            // Convert diagnostic to VerilintIssue format
            const issue = {
                line: selected.diagnostic.range.start.line + 1,
                column: selected.diagnostic.range.start.character + 1,
                code: String(selected.diagnostic.code) || 'UNKNOWN',
                message: selected.diagnostic.message,
                severity: selected.diagnostic.severity === vscode.DiagnosticSeverity.Error ? 'error' :
                    selected.diagnostic.severity === vscode.DiagnosticSeverity.Warning ? 'warning' : 'info',
                category: 'general'
            };
            vscode.commands.executeCommand('verilint.aiFix', editor.document, issue);
        }
    }));
    // Register commands
    context.subscriptions.push(vscode.commands.registerCommand('verilint.runLint', () => {
        const editor = vscode.window.activeTextEditor;
        if (editor && editor.document.languageId === 'verilog') {
            diagnosticsProvider.lintDocument(editor.document);
        }
        else {
            vscode.window.showWarningMessage('No Verilog file is currently open');
        }
    }));
    context.subscriptions.push(vscode.commands.registerCommand('verilint.runLintOnFile', async () => {
        const editor = vscode.window.activeTextEditor;
        if (editor) {
            await diagnosticsProvider.lintDocument(editor.document);
            vscode.window.showInformationMessage('Verilint check completed');
        }
    }));
    context.subscriptions.push(vscode.commands.registerCommand('verilint.clearDiagnostics', () => {
        diagnosticCollection.clear();
        outputChannel.appendLine('Diagnostics cleared');
    }));
    context.subscriptions.push(vscode.commands.registerCommand('verilint.showOutput', () => {
        outputChannel.show();
    }));
    // Register project check command
    context.subscriptions.push(vscode.commands.registerCommand('verilint.runProjectLint', async () => {
        // Get workspace folder
        const workspaceFolders = vscode.workspace.workspaceFolders;
        if (!workspaceFolders || workspaceFolders.length === 0) {
            vscode.window.showWarningMessage('No workspace folder open');
            return;
        }
        // Use first workspace folder as project path
        const workspacePath = workspaceFolders[0].uri.fsPath;
        // Get project root from config or use workspace root
        const config = vscode.workspace.getConfiguration('verilint');
        const projectRoot = config.get('projectRoot', '');
        const projectPath = projectRoot || workspacePath;
        // Show progress
        await vscode.window.withProgress({
            location: vscode.ProgressLocation.Notification,
            title: "Running Verilint project check...",
            cancellable: false
        }, async (progress) => {
            try {
                progress.report({ increment: 0, message: "Starting..." });
                const results = await linter.lintProject(projectPath, workspacePath);
                if (results) {
                    // Clear existing diagnostics
                    diagnosticCollection.clear();
                    // Process results for each file
                    let totalFiles = 0;
                    let totalIssues = 0;
                    for (const [filePath, result] of Object.entries(results)) {
                        totalFiles++;
                        totalIssues += result.totalIssues;
                        // Convert to VSCode diagnostics
                        const diagnostics = [];
                        for (const issue of result.issues) {
                            const line = Math.max(0, issue.line - 1);
                            const column = Math.max(0, issue.column - 1);
                            const range = new vscode.Range(line, column, line, 100);
                            let severity;
                            switch (issue.severity) {
                                case 'error':
                                    severity = vscode.DiagnosticSeverity.Error;
                                    break;
                                case 'warning':
                                    severity = vscode.DiagnosticSeverity.Warning;
                                    break;
                                case 'info':
                                    severity = vscode.DiagnosticSeverity.Information;
                                    break;
                                default:
                                    severity = vscode.DiagnosticSeverity.Hint;
                            }
                            const diagnostic = new vscode.Diagnostic(range, `[${issue.code}] ${issue.message}`, severity);
                            diagnostic.code = issue.code;
                            diagnostic.source = 'verilint';
                            diagnostics.push(diagnostic);
                        }
                        // Set diagnostics for this file
                        const fileUri = vscode.Uri.file(filePath);
                        diagnosticCollection.set(fileUri, diagnostics);
                    }
                    // Show summary
                    vscode.window.showInformationMessage(`Verilint project check complete: ${totalFiles} files, ${totalIssues} issues found`);
                    // Update code lens
                    codeLensProvider.clearIssues();
                    for (const [filePath, result] of Object.entries(results)) {
                        const fileUri = vscode.Uri.file(filePath);
                        codeLensProvider.setIssues(fileUri, result.issues);
                    }
                    progress.report({ increment: 100, message: "Complete" });
                }
                else {
                    vscode.window.showErrorMessage('Failed to run project check');
                }
            }
            catch (error) {
                vscode.window.showErrorMessage(`Project check error: ${error}`);
            }
        });
    }));
    // Register document change listeners
    const config = vscode.workspace.getConfiguration('verilint');
    if (config.get('runOnSave', true)) {
        context.subscriptions.push(vscode.workspace.onDidSaveTextDocument((document) => {
            if (document.languageId === 'verilog') {
                diagnosticsProvider.lintDocument(document);
            }
        }));
    }
    if (config.get('runOnType', false)) {
        let timeout;
        context.subscriptions.push(vscode.workspace.onDidChangeTextDocument((event) => {
            if (event.document.languageId !== 'verilog')
                return;
            if (timeout) {
                clearTimeout(timeout);
            }
            timeout = setTimeout(() => {
                diagnosticsProvider.lintDocument(event.document);
            }, 1000);
        }));
    }
    // Initial lint of open documents
    vscode.workspace.textDocuments.forEach((document) => {
        if (document.languageId === 'verilog') {
            diagnosticsProvider.lintDocument(document);
        }
    });
    // Status bar item
    const statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    statusBarItem.text = "$(shield) Verilint";
    statusBarItem.tooltip = "Click to run Verilint";
    statusBarItem.command = 'verilint.runLint';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);
    outputChannel.appendLine('Verilint initialized successfully');
}
exports.activate = activate;
function deactivate() {
    outputChannel?.appendLine('Verilint extension deactivated');
}
exports.deactivate = deactivate;
//# sourceMappingURL=extension.js.map