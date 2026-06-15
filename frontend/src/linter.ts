import * as vscode from 'vscode';
import { spawn } from 'child_process';
import * as path from 'path';
import * as fs from 'fs';

export interface VerilintIssue {
    line: number;
    column: number;
    code: string;
    message: string;
    severity: 'error' | 'warning' | 'info';
    category: string;
}

export interface VerilintResult {
    file: string;
    totalIssues: number;
    errors: number;
    warnings: number;
    infos: number;
    issues: VerilintIssue[];
}

export interface ProjectResult {
    [filePath: string]: VerilintResult;
}

export class VerilintLinter {
    private outputChannel: vscode.OutputChannel;

    constructor(outputChannel: vscode.OutputChannel) {
        this.outputChannel = outputChannel;
    }

    async lint(filePath: string, projectRoot?: string): Promise<VerilintResult | null> {
        const config = vscode.workspace.getConfiguration('verilint');
        const pythonPath = config.get<string>('pythonPath', 'python');

        // Find verilint_checker.py
        let linterPath = config.get<string>('executablePath', '');
        if (!linterPath) {
            // Try to find in workspace
            const workspaceFolders = vscode.workspace.workspaceFolders;
            if (workspaceFolders) {
                for (const folder of workspaceFolders) {
                    const possiblePaths = [
                        path.join(folder.uri.fsPath, 'final_lint', 'verilint_checker.py'),
                        path.join(folder.uri.fsPath, 'verilint_checker.py'),
                        path.join(folder.uri.fsPath, 'verilint', 'verilint_checker.py'),
                    ];
                    for (const p of possiblePaths) {
                        if (fs.existsSync(p)) {
                            linterPath = p;
                            break;
                        }
                    }
                    if (linterPath) break;
                }
            }
        }

        if (!linterPath || !fs.existsSync(linterPath)) {
            vscode.window.showErrorMessage(
                'Verilint checker not found. Please set verilint.executablePath in settings.'
            );
            return null;
        }

        // Find project root if not provided (workspace folder containing the file)
        if (!projectRoot) {
            const workspaceFolders = vscode.workspace.workspaceFolders;
            if (workspaceFolders) {
                for (const folder of workspaceFolders) {
                    if (filePath.startsWith(folder.uri.fsPath)) {
                        projectRoot = folder.uri.fsPath;
                        break;
                    }
                }
            }
        }

        // Build args: file to check + --project for module resolution
        const args: string[] = [linterPath, filePath, '--json'];
        if (projectRoot) {
            args.push('--project', projectRoot);
        }

        // Add include paths
        const includePaths = config.get<string[]>('includePaths', []);
        for (const includePath of includePaths) {
            args.push('-I', includePath);
        }

        // Add ignored error codes
        const ignoredCodes = config.get<string[]>('ignoredCodes', []);
        for (const code of ignoredCodes) {
            args.push('--ignore', code);
        }

        this.outputChannel.appendLine(`Running: ${pythonPath} ${args.map(a => `"${a}"`).join(' ')}`);

        return new Promise((resolve, reject) => {
            const process = spawn(pythonPath, args);
            let stdout = '';
            let stderr = '';

            process.stdout.on('data', (data) => {
                stdout += data.toString();
            });

            process.stderr.on('data', (data) => {
                stderr += data.toString();
            });

            process.on('close', (code) => {
                if (stderr) {
                    this.outputChannel.appendLine(`Stderr: ${stderr}`);
                }

                // Extract JSON from stdout (may have debug output before/after it)
                let jsonStr = stdout;
                const jsonStart = stdout.indexOf('{');
                const jsonEnd = stdout.lastIndexOf('}');
                if (jsonStart >= 0 && jsonEnd > jsonStart) {
                    jsonStr = stdout.substring(jsonStart, jsonEnd + 1);
                }

                this.outputChannel.appendLine(`Trying to parse JSON, length: ${jsonStr.length}`);
                this.outputChannel.appendLine(`JSON preview: ${jsonStr.substring(0, 100)}...`);

                try {
                    const result = JSON.parse(jsonStr) as VerilintResult;
                    this.outputChannel.appendLine(
                        `Found ${result.totalIssues} issues (${result.errors} errors, ${result.warnings} warnings, ${result.infos} info)`
                    );
                    resolve(result);
                } catch (e) {
                    this.outputChannel.appendLine(`Failed to parse output: ${e}`);
                    this.outputChannel.appendLine(`Raw stdout: ${stdout.substring(0, 200)}`);
                    resolve(null);
                }
            });

            process.on('error', (err) => {
                this.outputChannel.appendLine(`Error running linter: ${err.message}`);
                reject(err);
            });
        });
    }

    async lintProject(projectPath: string, projectRoot?: string): Promise<ProjectResult | null> {
        const config = vscode.workspace.getConfiguration('verilint');
        const pythonPath = config.get<string>('pythonPath', 'python');

        // Find verilint_checker.py
        let linterPath = config.get<string>('executablePath', '');
        if (!linterPath) {
            const workspaceFolders = vscode.workspace.workspaceFolders;
            if (workspaceFolders) {
                for (const folder of workspaceFolders) {
                    const possiblePaths = [
                        path.join(folder.uri.fsPath, 'final_lint', 'verilint_checker.py'),
                        path.join(folder.uri.fsPath, 'verilint_checker.py'),
                        path.join(folder.uri.fsPath, 'verilint', 'verilint_checker.py'),
                    ];
                    for (const p of possiblePaths) {
                        if (fs.existsSync(p)) {
                            linterPath = p;
                            break;
                        }
                    }
                    if (linterPath) break;
                }
            }
        }

        if (!linterPath || !fs.existsSync(linterPath)) {
            vscode.window.showErrorMessage(
                'Verilint checker not found. Please set verilint.executablePath in settings.'
            );
            return null;
        }

        const rootPath = projectRoot || projectPath;
        const args: string[] = [linterPath, projectPath, '--project-only', '--json', '--root', rootPath];

        const includePaths = config.get<string[]>('includePaths', []);
        for (const includePath of includePaths) {
            args.push('-I', includePath);
        }

        const recursive = config.get<boolean>('projectRecursive', true);
        if (!recursive) {
            args.push('--no-recursive');
        }

        // Add ignored error codes
        const ignoredCodes = config.get<string[]>('ignoredCodes', []);
        for (const code of ignoredCodes) {
            args.push('--ignore', code);
        }

        this.outputChannel.appendLine(`Running project check: ${pythonPath} ${args.map(a => `"${a}"`).join(' ')}`);
        this.outputChannel.appendLine(`Working directory: ${projectPath}`);
        this.outputChannel.appendLine(`Linter path: ${linterPath}`);

        return new Promise((resolve, reject) => {
            const process = spawn(pythonPath, args);
            let stdout = '';
            let stderr = '';

            process.stdout.on('data', (data) => {
                stdout += data.toString();
            });

            process.stderr.on('data', (data) => {
                stderr += data.toString();
            });

            process.on('close', (code) => {
                if (stderr) {
                    this.outputChannel.appendLine(`Stderr: ${stderr}`);
                }

                try {
                    const results: ProjectResult = {};
                    const lines = stdout.split('\n').filter(line => line.trim());

                    for (const line of lines) {
                        try {
                            const result = JSON.parse(line) as VerilintResult;
                            if (result.file) {
                                results[result.file] = result;
                            }
                        } catch (e) {
                            // Skip non-JSON lines
                        }
                    }

                    const totalFiles = Object.keys(results).length;
                    const totalIssues = Object.values(results).reduce((sum, r) => sum + r.totalIssues, 0);
                    this.outputChannel.appendLine(
                        `Project check complete: ${totalFiles} files, ${totalIssues} issues`
                    );
                    resolve(results);
                } catch (e) {
                    this.outputChannel.appendLine(`Failed to parse output: ${stdout}`);
                    resolve(null);
                }
            });

            process.on('error', (err) => {
                this.outputChannel.appendLine(`Error running linter: ${err.message}`);
                reject(err);
            });
        });
    }
}
