// next-agent: abre, na barra lateral do app, o primeiro chat com respostas não lidas e foca a
// caixa de mensagem dele, para o ditado colar ali.
//
// Lê a árvore de Acessibilidade: cada chat da barra lateral é um AXCheckBox, e o contador de
// não lidas aparece como um texto "N nova(s) resposta(s)" (ou "N new ...") dentro dele.
// Uso: next-agent [--dry-run] [App]   (padrão: OpenBot; --dry-run só mostra, não abre)
// Saída: o nome do chat aberto. Código 0 = abriu, 1 = nenhum não lido, 2 = erro.
import AppKit
import ApplicationServices

let args = CommandLine.arguments.dropFirst()
let dryRun = args.contains("--dry-run")
let appName = args.first(where: { !$0.hasPrefix("--") }) ?? "OpenBot"
let unread = try! NSRegularExpression(pattern: #"^\d+\s+(nova|novas|new)\b"#, options: [.caseInsensitive])
let composer = try! NSRegularExpression(pattern: #"^(mensagem para|message)\b"#, options: [.caseInsensitive])

func attr(_ el: AXUIElement, _ name: String) -> AnyObject? {
    var value: AnyObject?
    return AXUIElementCopyAttributeValue(el, name as CFString, &value) == .success ? value : nil
}

func children(_ el: AXUIElement) -> [AXUIElement] {
    (attr(el, "AXChildren") as? [AXUIElement]) ?? []
}

func texts(_ el: AXUIElement, _ depth: Int = 0) -> [String] {
    if depth > 4 { return [] }
    var out: [String] = []
    if (attr(el, "AXRole") as? String) == "AXStaticText", let v = attr(el, "AXValue") as? String { out.append(v) }
    for child in children(el) { out += texts(child, depth + 1) }
    return out
}

// Primeiro AXCheckBox (na ordem da barra lateral) que tem um contador de não lidas.
func findUnread(_ el: AXUIElement, _ depth: Int = 0) -> (AXUIElement, String)? {
    if depth > 40 { return nil }
    if (attr(el, "AXRole") as? String) == "AXCheckBox" {
        let t = texts(el)
        if t.contains(where: { unread.firstMatch(in: $0, range: NSRange($0.startIndex..., in: $0)) != nil }) {
            return (el, t.first ?? "?")
        }
        return nil
    }
    for child in children(el) {
        if let hit = findUnread(child, depth + 1) { return hit }
    }
    return nil
}

// Caixa de mensagem do chat: AXTextArea com descrição "Mensagem para <agente> em <conversa>".
func findComposer(_ el: AXUIElement, _ depth: Int = 0) -> AXUIElement? {
    if depth > 60 { return nil }
    if (attr(el, "AXRole") as? String) == "AXTextArea", let d = attr(el, "AXDescription") as? String,
       composer.firstMatch(in: d, range: NSRange(d.startIndex..., in: d)) != nil {
        return el
    }
    for child in children(el) {
        if let hit = findComposer(child, depth + 1) { return hit }
    }
    return nil
}

func composerInWindows(_ app: AXUIElement) -> AXUIElement? {
    for window in (attr(app, "AXWindows") as? [AXUIElement]) ?? [] {
        if let c = findComposer(window) { return c }
    }
    return nil
}

guard let running = NSWorkspace.shared.runningApplications.first(where: { $0.localizedName == appName }) else {
    FileHandle.standardError.write("\(appName) não está aberto\n".data(using: .utf8)!)
    exit(2)
}
guard AXIsProcessTrusted() else {
    FileHandle.standardError.write("sem permissão de Acessibilidade\n".data(using: .utf8)!)
    exit(2)
}
let app = AXUIElementCreateApplication(running.processIdentifier)
// Apps Electron só expõem a página para a Acessibilidade depois deste pedido.
AXUIElementSetAttributeValue(app, "AXManualAccessibility" as CFString, kCFBooleanTrue)

var hit: (AXUIElement, String)?
for attempt in 0..<5 {  // na primeira vez a árvore leva um instante para aparecer
    for window in (attr(app, "AXWindows") as? [AXUIElement]) ?? [] {
        if let found = findUnread(window) { hit = found; break }
    }
    if hit != nil || attempt == 4 { break }
    Thread.sleep(forTimeInterval: 0.3)
}
guard let (chat, name) = hit else { exit(1) }
if dryRun { print(name); exit(0) }
let before = composerInWindows(app).flatMap { attr($0, "AXDescription") as? String }
guard AXUIElementPerformAction(chat, kAXPressAction as CFString) == .success else {
    FileHandle.standardError.write("não consegui abrir \(name)\n".data(using: .utf8)!)
    exit(2)
}
// Foca a caixa de mensagem do chat aberto, para o ditado colar nela. Espera a troca de chat
// (a descrição muda para o novo agente); se não mudar a tempo, foca a caixa que houver.
var box: AXUIElement?
for _ in 0..<10 {
    Thread.sleep(forTimeInterval: 0.15)
    if let c = composerInWindows(app), (attr(c, "AXDescription") as? String) != before { box = c; break }
}
let focused = (box ?? composerInWindows(app)).map {
    AXUIElementSetAttributeValue($0, kAXFocusedAttribute as CFString, kCFBooleanTrue) == .success
} ?? false
if !focused {
    FileHandle.standardError.write("abri \(name), mas não consegui focar a caixa de mensagem\n".data(using: .utf8)!)
}
print(name)
