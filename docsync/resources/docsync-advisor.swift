// docsync-advisor — on-device Apple Foundation Models helper for `docsync advise`.
// stdin: prompt. stdout: the model's answer. Exit 0 ok, 1 model unavailable, 2 timeout/error.
// `--check` prints the availability and exits 0 only when the model can be used.
import Foundation
import FoundationModels

let instructions = """
You are the advisor built into docsync, a tool that keeps ~/Documents in sync between Macs \
over the LAN with rsync. You get a sync report, conflict file names and warnings. Answer in \
German, at most 8 short bullet points, each starting with '•'. Recommend only actions the user \
can do with the docsync commands or by deleting/keeping files that are named in the input. \
Never invent file paths, hosts or commands that are not in the input. Judge conflict copies by \
size and timestamp only, never by guessing their content: sizes and timestamps are given in \
the input. Keep the largest version unless the input says otherwise; a much smaller version \
with a timestamp equal to the sync time is a truncated transfer. A name containing \
'.conflict-' twice is a conflict copy of a conflict copy from an interrupted run — compare its \
size with the others, do not assume it is broken. If deletions were held back by the deletion \
guard, say whether they look like a rename or case change rather than real deletions.
"""

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

@main
struct Advisor {
    static func main() async {
        let model = SystemLanguageModel.default
        let (text, ok) = availabilityText(model)
        if CommandLine.arguments.contains("--check") {
            print(text)
            exit(ok ? 0 : 1)
        }
        guard ok else {
            FileHandle.standardError.write(("model unavailable: " + text + "\n").data(using: .utf8)!)
            exit(1)
        }
        let data = FileHandle.standardInput.readDataToEndOfFile()
        guard let prompt = String(data: data, encoding: .utf8), !prompt.isEmpty else {
            FileHandle.standardError.write("empty prompt\n".data(using: .utf8)!)
            exit(2)
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + 30) {
            FileHandle.standardError.write("timeout after 30 s\n".data(using: .utf8)!)
            exit(2)
        }
        let session = LanguageModelSession(model: model, instructions: instructions)
        do {
            let response = try await session.respond(to: prompt)
            print(response.content)
            exit(0)
        } catch {
            FileHandle.standardError.write("model error: \(error)\n".data(using: .utf8)!)
            exit(2)
        }
    }
}
