// docsync-icloud <folder> [pattern ...]: lists the files below <folder> that iCloud has not uploaded yet,
// one "<mtime seconds>\t<path relative to folder>\0" record each. Entries whose name matches a pattern
// (fnmatch, docsync's excludes) are skipped, directories with their whole subtree. Reads only URL
// resource values (metadata): never file contents, never triggers a download. Built on first use by
// docsync/icloud.py (swiftc).
import Foundation

let args = CommandLine.arguments
guard args.count >= 2 else {
    FileHandle.standardError.write("usage: docsync-icloud <folder> [pattern ...]\n".data(using: .utf8)!)
    exit(2)
}
let root = URL(fileURLWithPath: args[1], isDirectory: true).standardizedFileURL
let prefix = root.path.hasSuffix("/") ? root.path : root.path + "/"
let patterns = Array(args.dropFirst(2))
let keys: [URLResourceKey] = [.isRegularFileKey, .isUbiquitousItemKey, .ubiquitousItemIsUploadedKey,
                              .contentModificationDateKey]
let keySet = Set(keys)
guard let walker = FileManager.default.enumerator(at: root, includingPropertiesForKeys: keys, options: [],
                                                  errorHandler: { _, _ in true }) else {
    FileHandle.standardError.write("cannot list \(root.path)\n".data(using: .utf8)!)
    exit(1)
}
let stdout = FileHandle.standardOutput
var out = Data()
for case let url as URL in walker {
    let name = url.lastPathComponent
    if patterns.contains(where: { fnmatch($0, name, 0) == 0 }) {
        walker.skipDescendants()
        continue
    }
    guard let v = try? url.resourceValues(forKeys: keySet), v.isRegularFile == true, v.isUbiquitousItem == true,
          v.ubiquitousItemIsUploaded == false else { continue }
    let rel = url.path.hasPrefix(prefix) ? String(url.path.dropFirst(prefix.count)) : url.path
    out.append("\(Int(v.contentModificationDate?.timeIntervalSince1970 ?? 0))\t\(rel)\0".data(using: .utf8)!)
    if out.count > 1 << 16 {
        stdout.write(out)
        out.removeAll()
    }
}
stdout.write(out)
