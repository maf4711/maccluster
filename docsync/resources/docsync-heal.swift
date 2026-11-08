// docsync-heal — on-device Apple Foundation Models helper for `docsync heal`.
// `choose`: stdin = alerts + allowed actions (text), stdout = JSON {"decisions":[{code, action, reason}]}
//           built by guided generation, so the shape is fixed; Python still validates every action.
// `summarize`: stdin = purpose + text, stdout = a German summary of at most 3 sentences.
// `--check`: prints the availability, exit 0 only when the model can be used.
// Exit 0 ok, 1 model unavailable, 2 timeout/error.
import Foundation
import FoundationModels

let chooseInstructions = """
You are the self-healing planner built into docsync, a tool that syncs folders between Macs. \
You get alerts (node, code, text) and, per alert code, the list of allowed actions. For every \
alert code pick exactly one action from that code's allowed list, or "none" when no action \
helps. Never invent an action, a code, a command, a path or a host. Prefer the first allowed \
action unless the alert text says it cannot help. The reason is one short German sentence.
"""

let summarizeInstructions = """
You summarize diagnostics for docsync, a tool that syncs folders between Macs. Always answer in \
German with complete sentences, at most 3, plain text without bullet points or lists. Say what \
the input shows, for example: "Der Hauptthread wartete in transport.py (Zeile 301) auf rsync." \
Name only files, functions, hosts and numbers that appear in the input. Never recommend \
deleting files.
"""

@Generable
struct Decision {
    @Guide(description: "the alert code exactly as given in the input")
    var code: String
    @Guide(description: "one action from the allowed list of this code, or none")
    var action: String
    @Guide(description: "one short German sentence why")
    var reason: String
}

@Generable
struct Plan {
    @Guide(description: "one decision per alert code of the input")
    var decisions: [Decision]
}

func availabilityText(_ model: SystemLanguageModel) -> (String, Bool) {
    switch model.availability {
    case .available:
        return ("available", true)
    case .unavailable(let reason):
        switch reason {
        case .deviceNotEligible: return ("device not eligible for Apple Intelligence", false)
        case .appleIntelligenceNotEnabled: return ("Apple Intelligence is off in System Settings", false)
        case .modelNotReady: return ("model not ready (still downloading)", false)
        @unknown default: return ("unavailable", false)
        }
    }
}

func fail(_ text: String, _ code: Int32) -> Never {
    FileHandle.standardError.write((text + "\n").data(using: .utf8)!)
    exit(code)
}

@main
struct Heal {
    static func main() async {
        let model = SystemLanguageModel.default
        let (text, ok) = availabilityText(model)
        let mode = CommandLine.arguments.dropFirst().first ?? ""
        if mode == "--check" {
            print(text)
            exit(ok ? 0 : 1)
        }
        guard ok else { fail("model unavailable: " + text, 1) }
        guard mode == "choose" || mode == "summarize" else { fail("usage: docsync-heal choose|summarize|--check", 2) }
        let data = FileHandle.standardInput.readDataToEndOfFile()
        guard let prompt = String(data: data, encoding: .utf8), !prompt.isEmpty else { fail("empty prompt", 2) }
        DispatchQueue.global().asyncAfter(deadline: .now() + 19) { fail("timeout after 19 s", 2) }
        do {
            if mode == "choose" {
                let session = LanguageModelSession(model: model, instructions: chooseInstructions)
                let plan = try await session.respond(to: prompt, generating: Plan.self).content
                let rows = plan.decisions.map { ["code": $0.code, "action": $0.action, "reason": $0.reason] }
                let json = try JSONSerialization.data(withJSONObject: ["decisions": rows], options: [.sortedKeys])
                print(String(data: json, encoding: .utf8)!)
            } else {
                let session = LanguageModelSession(model: model, instructions: summarizeInstructions)
                print(try await session.respond(to: prompt).content)
            }
            exit(0)
        } catch {
            fail("model error: \(error)", 2)
        }
    }
}
