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
exports.VerilintDiagnosticsProvider = void 0;
const vscode = __importStar(require("vscode"));
class VerilintDiagnosticsProvider {
    constructor(linter, outputChannel) {
        this.linter = linter;
        this.outputChannel = outputChannel;
    }
    setDiagnosticCollection(collection) {
        this.diagnosticCollection = collection;
    }
    setCodeLensProvider(provider) {
        this.codeLensProvider = provider;
    }
    async lintDocument(document) {
        if (!this.diagnosticCollection) {
            return;
        }
        const config = vscode.workspace.getConfiguration('verilint');
        const showInfo = config.get('showInfo', false);
        this.outputChannel.appendLine(`\nLinting: ${document.fileName}`);
        // Find project root (workspace folder containing the file)
        let projectRoot = config.get('projectRoot', '');
        this.outputChannel.appendLine(`Config projectRoot: '${projectRoot}'`);
        if (!projectRoot) {
            const workspaceFolders = vscode.workspace.workspaceFolders;
            if (workspaceFolders) {
                for (const folder of workspaceFolders) {
                    if (document.fileName.startsWith(folder.uri.fsPath)) {
                        projectRoot = folder.uri.fsPath;
                        break;
                    }
                }
            }
        }
        this.outputChannel.appendLine(`Using projectRoot: '${projectRoot}'`);
        try {
            this.outputChannel.appendLine(`Document fileName: ${document.fileName}`);
            const result = await this.linter.lint(document.fileName, projectRoot);
            if (!result) {
                this.outputChannel.appendLine(`No result from linter`);
                return;
            }
            this.outputChannel.appendLine(`Result file: ${result.file}`);
            this.outputChannel.appendLine(`Total issues: ${result.totalIssues}`);
            const diagnostics = [];
            for (const issue of result.issues) {
                const diagnostic = this.createDiagnostic(document, issue);
                diagnostics.push(diagnostic);
            }
            this.outputChannel.appendLine(`Setting ${diagnostics.length} diagnostics for ${document.uri.toString()}`);
            this.outputChannel.appendLine(`Document URI scheme: ${document.uri.scheme}, path: ${document.uri.fsPath}`);
            if (diagnostics.length > 0) {
                this.outputChannel.appendLine(`First diagnostic: line ${diagnostics[0].range.start.line}, msg: ${diagnostics[0].message.substring(0, 50)}`);
            }
            // 确保使用正确的 URI
            const uri = vscode.Uri.file(document.fileName);
            this.outputChannel.appendLine(`Using URI: ${uri.toString()}`);
            this.diagnosticCollection.set(uri, diagnostics);
            // 验证诊断是否设置成功
            const check = this.diagnosticCollection.get(uri);
            this.outputChannel.appendLine(`Verification: ${check?.length || 0} diagnostics in collection`);
            // Update code lens issues
            if (this.codeLensProvider) {
                // Filter issues to match what we're showing in diagnostics
                const shownIssues = result.issues.filter(issue => !(issue.severity === 'info' && !showInfo));
                this.codeLensProvider.setIssues(document.uri, shownIssues);
            }
            // Show summary
            if (result.totalIssues > 0) {
                const summary = `Verilint: ${result.errors} errors, ${result.warnings} warnings, ${result.infos} info`;
                if (result.errors > 0) {
                    vscode.window.setStatusBarMessage(`$(error) ${summary}`, 5000);
                }
                else if (result.warnings > 0) {
                    vscode.window.setStatusBarMessage(`$(warning) ${summary}`, 5000);
                }
                else {
                    vscode.window.setStatusBarMessage(`$(info) ${summary}`, 5000);
                }
            }
            else {
                vscode.window.setStatusBarMessage(`$(check) Verilint: No issues found`, 3000);
            }
        }
        catch (error) {
            this.outputChannel.appendLine(`Error: ${error}`);
        }
    }
    /**
     * Run project-level linting and update diagnostics for all files
     */
    async lintProject(document) {
        if (!this.diagnosticCollection) {
            return;
        }
        // Get workspace folder
        const workspaceFolders = vscode.workspace.workspaceFolders;
        if (!workspaceFolders || workspaceFolders.length === 0) {
            return;
        }
        const config = vscode.workspace.getConfiguration('verilint');
        const showInfo = config.get('showInfo', false);
        // Use project root from config or workspace root
        const workspacePath = workspaceFolders[0].uri.fsPath;
        const projectRoot = config.get('projectRoot', '');
        const projectPath = projectRoot || workspacePath;
        this.outputChannel.appendLine(`\nRunning project check: ${projectPath}`);
        try {
            const results = await this.linter.lintProject(projectPath, workspacePath);
            if (!results) {
                return;
            }
            // Clear existing diagnostics
            this.diagnosticCollection.clear();
            // Process results for each file
            let totalFiles = 0;
            let totalIssues = 0;
            let totalErrors = 0;
            let totalWarnings = 0;
            let totalInfos = 0;
            for (const [filePath, result] of Object.entries(results)) {
                totalFiles++;
                totalIssues += result.totalIssues;
                totalErrors += result.errors;
                totalWarnings += result.warnings;
                totalInfos += result.infos;
                // Create URI for this file
                const fileUri = vscode.Uri.file(filePath);
                // Convert to VSCode diagnostics
                const diagnostics = [];
                for (const issue of result.issues) {
                    // Skip INFO diagnostics if configured
                    if (issue.severity === 'info' && !showInfo) {
                        continue;
                    }
                    const diagnostic = this.createDiagnosticForFile(filePath, issue);
                    diagnostics.push(diagnostic);
                }
                // Set diagnostics for this file
                this.diagnosticCollection.set(fileUri, diagnostics);
                // Update code lens for this file
                if (this.codeLensProvider) {
                    const shownIssues = result.issues.filter(issue => !(issue.severity === 'info' && !showInfo));
                    this.codeLensProvider.setIssues(fileUri, shownIssues);
                }
            }
            // Show summary
            if (totalIssues > 0) {
                const summary = `Verilint: ${totalErrors} errors, ${totalWarnings} warnings, ${totalInfos} info in ${totalFiles} files`;
                if (totalErrors > 0) {
                    vscode.window.setStatusBarMessage(`$(error) ${summary}`, 5000);
                }
                else if (totalWarnings > 0) {
                    vscode.window.setStatusBarMessage(`$(warning) ${summary}`, 5000);
                }
                else {
                    vscode.window.setStatusBarMessage(`$(info) ${summary}`, 5000);
                }
            }
            else {
                vscode.window.setStatusBarMessage(`$(check) Verilint: No issues found in ${totalFiles} files`, 3000);
            }
            // If a specific document was passed, update its code lens focus
            if (document && this.codeLensProvider) {
                const docResult = results[document.fileName];
                if (docResult) {
                    const shownIssues = docResult.issues.filter(issue => !(issue.severity === 'info' && !showInfo));
                    this.codeLensProvider.setIssues(document.uri, shownIssues);
                }
            }
        }
        catch (error) {
            this.outputChannel.appendLine(`Error: ${error}`);
        }
    }
    createDiagnostic(document, issue) {
        const line = Math.max(0, issue.line - 1); // Convert to 0-based
        const column = Math.max(0, issue.column - 1);
        // Find the end of the line for the range
        const lineText = document.lineAt(line).text;
        const endColumn = lineText.length;
        const range = new vscode.Range(line, column, line, endColumn);
        return this.createDiagnosticInternal(range, issue);
    }
    createDiagnosticForFile(filePath, issue) {
        const line = Math.max(0, issue.line - 1); // Convert to 0-based
        const column = Math.max(0, issue.column - 1);
        // Use a default end column since we don't have the document open
        const endColumn = 100;
        const range = new vscode.Range(line, column, line, endColumn);
        return this.createDiagnosticInternal(range, issue);
    }
    createDiagnosticInternal(range, issue) {
        const severity = this.mapSeverity(issue.severity);
        const diagnostic = new vscode.Diagnostic(range, `[${issue.code}] ${issue.message}`, severity);
        diagnostic.code = issue.code;
        diagnostic.source = 'verilint';
        // Add tags for unused/deprecated hints
        if (issue.code === 'REG002') { // DRIVE_WITHOUT_USE
            diagnostic.tags = [vscode.DiagnosticTag.Unnecessary];
        }
        return diagnostic;
    }
    mapSeverity(severity) {
        switch (severity) {
            case 'error':
                return vscode.DiagnosticSeverity.Error;
            case 'warning':
                return vscode.DiagnosticSeverity.Warning;
            case 'info':
                return vscode.DiagnosticSeverity.Information;
            default:
                return vscode.DiagnosticSeverity.Hint;
        }
    }
}
exports.VerilintDiagnosticsProvider = VerilintDiagnosticsProvider;
//# sourceMappingURL=diagnostics.js.map