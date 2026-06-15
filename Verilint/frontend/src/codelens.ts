import * as vscode from 'vscode';
import { VerilintIssue } from './linter';

export class VerilintCodeLensProvider implements vscode.CodeLensProvider {
    private _onDidChangeCodeLenses: vscode.EventEmitter<void> = new vscode.EventEmitter<void>();
    public readonly onDidChangeCodeLenses: vscode.Event<void> = this._onDidChangeCodeLenses.event;

    private issues: Map<string, VerilintIssue[]> = new Map();

    provideCodeLenses(document: vscode.TextDocument, token: vscode.CancellationToken): vscode.CodeLens[] {
        if (document.languageId !== 'verilog') {
            return [];
        }

        const codeLenses: vscode.CodeLens[] = [];

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
    setIssues(documentUri: vscode.Uri, issues: VerilintIssue[]) {
        this.issues.set(documentUri.toString(), issues);
        this._onDidChangeCodeLenses.fire();
    }

    /**
     * Clear issues for a document or all documents
     */
    clearIssues(documentUri?: vscode.Uri) {
        if (documentUri) {
            this.issues.delete(documentUri.toString());
        } else {
            this.issues.clear();
        }
        this._onDidChangeCodeLenses.fire();
    }
}
