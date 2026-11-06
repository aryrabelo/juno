// Juno.app: roda o Juno em segundo plano, sem janela. O install.sh monta o app em
// ~/Library/Application Support/Juno/Juno.app e o LaunchAgent com.aryrabelo.juno o abre no
// login (e de novo se ele parar).
//
// Por que um app e não o python direto: o macOS dá Microfone, Acessibilidade e Automação a
// quem é o "responsável" pelo processo. No Terminal era o Terminal; aqui é este app, "Juno",
// que tem o texto do pedido do microfone no Info.plist. O python é aberto como filho e herda
// as permissões, como herdava do Terminal. O python sozinho não tem esse texto, e o macOS
// negaria o microfone sem perguntar nada.
//
// Uso: Juno <pasta do juno> [argumentos do juno.py, ex.: --test]
// Saída e erros vão para onde o launchd mandar (~/Library/Logs/Juno/juno.log).
import ApplicationServices
import Foundation

let args = CommandLine.arguments
guard args.count > 1 else {
    FileHandle.standardError.write("uso: Juno <pasta do juno> [argumentos do juno.py]\n".data(using: .utf8)!)
    exit(2)
}
let dir = URL(fileURLWithPath: args[1])

// O juno.log nunca para de crescer: acima de 5 MB, começa de novo (o launchd abre com O_APPEND).
var info = stat()
if fstat(1, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG, info.st_size > 5_000_000 { ftruncate(1, 0) }

let stamp = DateFormatter()
stamp.dateFormat = "yyyy-MM-dd HH:mm:ss"
print("--- Juno.app \(stamp.string(from: Date())): abrindo o juno.py em \(dir.path)")

// Pede a Acessibilidade em nome do "Juno": o macOS mostra o aviso e põe o Juno na lista de
// Ajustes do Sistema › Privacidade e Segurança › Acessibilidade, só falta ligar.
if !AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue(): true] as CFDictionary) {
    print("Juno.app: sem Acessibilidade ainda; ligue o Juno em Ajustes do Sistema › Privacidade e Segurança › Acessibilidade")
}
fflush(stdout)

let juno = Process()
juno.executableURL = dir.appendingPathComponent(".venv/bin/python")
juno.arguments = ["-W", "ignore", dir.appendingPathComponent("juno.py").path] + args.dropFirst(2)
juno.currentDirectoryURL = dir
// Se este app morrer sem repassar nada (kill -9), o juno.py vê que o pai mudou e sai sozinho.
var env = ProcessInfo.processInfo.environment
env["JUNO_LAUNCHER_PID"] = String(getpid())
juno.environment = env
juno.terminationHandler = { p in
    exit(p.terminationReason == .uncaughtSignal ? 128 + p.terminationStatus : p.terminationStatus)
}
do {
    try juno.run()
} catch {
    print("Juno.app: não consegui abrir \(juno.executableURL!.path): \(error)")
    exit(1)
}

// launchctl bootout (ou o logout) manda SIGTERM a este app: repassa ao python e sai junto.
// Ignorado só depois de abrir o filho, para ele nascer com o SIGTERM normal.
var stops: [DispatchSourceSignal] = []
for sig in [SIGTERM, SIGINT, SIGHUP] {
    signal(sig, SIG_IGN)
    let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
    source.setEventHandler { kill(juno.processIdentifier, sig) }
    source.resume()
    stops.append(source)
}
dispatchMain()
