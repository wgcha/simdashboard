import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const workspaceDir = path.resolve(frontendDir, '..')
const windowsRuntime = path.join(workspaceDir, '.venv-runtime', 'Scripts', 'python.exe')
const windowsVenv = path.join(workspaceDir, '.venv', 'Scripts', 'python.exe')
const unixWslVenv = path.join(workspaceDir, '.venv-wsl', 'bin', 'python')
const unixRuntime = path.join(workspaceDir, '.venv-runtime', 'bin', 'python')
const unixVenv = path.join(workspaceDir, '.venv', 'bin', 'python')
const python = process.env.API_SCHEMA_PYTHON
  ?? (process.platform === 'win32'
    ? (existsSync(windowsRuntime) ? windowsRuntime : windowsVenv)
    : (existsSync(unixWslVenv) ? unixWslVenv : (existsSync(unixRuntime) ? unixRuntime : (existsSync(unixVenv) ? unixVenv : 'python3'))))
const openapiFile = path.join(frontendDir, 'openapi.json')
const generatedFile = path.join(frontendDir, 'src', 'shared', 'api', 'generated', 'openapi.ts')
const openapiTypescriptCli = path.join(frontendDir, 'node_modules', 'openapi-typescript', 'bin', 'cli.js')

function run(command, args, cwd) {
  const result = spawnSync(command, args, { cwd, stdio: 'inherit' })
  if (result.status !== 0) process.exit(result.status ?? 1)
}

run(python, [path.join(workspaceDir, 'backend', 'scripts', 'export_openapi.py'), '--output', openapiFile], path.join(workspaceDir, 'backend'))
run(process.execPath, [openapiTypescriptCli, openapiFile, '--output', generatedFile], frontendDir)
