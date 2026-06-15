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
exports.VerilintCodeLensProvider = void 0;
const vscode = __importStar(require("vscode"));
class VerilintCodeLensProvider {
    constructor() {
        this._onDidChangeCodeLenses = new vscode.EventEmitter();
        this.onDidChangeCodeLenses = this._onDidChangeCodeLenses.event;
        this.issues = new Map();
    }
    provideCodeLenses(document, token) {
        if (document.languageId !== 'verilog') {
            return [];
        }
        const codeLenses = [];
        // Add a code lens at the top of the file
        const range = new vscode.Range(0, 0, 0, 0);
        const runLintCommand = new vscode.CodeLens(range, {
            title: '$(play) Run Verilint',
            tooltip: 'Run Verilint on this file',
            command: 'verilint.runLintOnFile',
            arguments: [document]
        });
        codeLenses.push(runLintCommand);
        // Add AI Fix button at the top
        const aiFixCommand = new vscode.CodeLens(range, {
            title: '$(sparkle) AI Fix Issues',
            tooltip: 'Select an issue to get AI fix suggestion',
            command: 'verilint.showIssuesAndFix',
            arguments: [document]
        });
        codeLenses.push(aiFixCommand);
        // Add AI Fix buttons for each issue
        const fileIssues = this.issues.get(document.uri.toString()) || [];
        for (const issue of fileIssues) {
            const line = Math.max(0, issue.line - 1);
            const issueRange = new vscode.Range(line, 0, line, 0);
            const fixCommand = new vscode.CodeLens(issueRange, {
                title: `$(sparkle) AI Fix: ${issue.message.substring(0, 30)}...`,
                tooltip: `Get AI fix suggestion for: ${issue.message}`,
                command: 'verilint.aiFix',
                arguments: [document, issue]
            });
            codeLenses.push(fixCommand);
        }
        return codeLenses;
    }
    /**
     * Update issues and refresh code lenses
     */
    setIssues(documentUri, issues) {
        this.issues.set(documentUri.toString(), issues);
        this._onDidChangeCodeLenses.fire();
    }
    /**
     * Clear issues for a document or all documents
     */
    clearIssues(documentUri) {
        if (documentUri) {
            this.issues.delete(documentUri.toString());
        }
        else {
            this.issues.clear();
        }
        this._onDidChangeCodeLenses.fire();
    }
}
exports.VerilintCodeLensProvider = VerilintCodeLensProvider;
//# sourceMappingURL=codelens.js.map