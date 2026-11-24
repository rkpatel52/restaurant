import CoreGraphics
import CoreText
import Foundation
import ImageIO

struct Receipt: Decodable {
    let number: String
    let table: String
    let date: String
    let payment: String
    let total: String
    let reward: String
    let items: [Item]

    struct Item: Decodable {
        let name: String
        let quantity: Int
        let amount: String
    }
}

let receipt = try JSONDecoder().decode(Receipt.self, from: FileHandle.standardInput.readDataToEndOfFile())
let width = 900
let ink = CGColor(red: 0.09, green: 0.17, blue: 0.16, alpha: 1)
let accent = CGColor(red: 0.10, green: 0.35, blue: 0.27, alpha: 1)
let pale = CGColor(red: 0.92, green: 0.96, blue: 0.94, alpha: 1)

func lines(_ text: String, size: CGFloat, width: Double, bold: Bool = false) -> [CTLine] {
    let font = CTFontCreateWithName((bold ? "Helvetica-Bold" : "Helvetica") as CFString, size, nil)
    let attributed = NSAttributedString(
        string: text,
        attributes: [NSAttributedString.Key(kCTFontAttributeName as String): font,
                     NSAttributedString.Key(kCTForegroundColorAttributeName as String): ink]
    )
    let typesetter = CTTypesetterCreateWithAttributedString(attributed)
    var result: [CTLine] = []
    var index = 0
    while index < attributed.length {
        let count = max(1, CTTypesetterSuggestLineBreak(typesetter, index, width))
        result.append(CTTypesetterCreateLine(typesetter, CFRange(location: index, length: count)))
        index += count
    }
    return result
}

let descriptions = receipt.items.map { lines("\($0.quantity) × \($0.name)", size: 24, width: 505) }
let tableLines = lines(receipt.table, size: 25, width: 780)
let height = 400 + tableLines.count * 32 + descriptions.reduce(0) { $0 + $1.count * 32 + 24 }
guard height <= 16000,
      let canvas = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                             bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                             bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
else { exit(1) }
canvas.setFillColor(CGColor(gray: 1, alpha: 1))
canvas.fill(CGRect(x: 0, y: 0, width: width, height: height))
canvas.setFillColor(pale)
canvas.fill(CGRect(x: 0, y: height - 130, width: width, height: 130))

func draw(_ line: CTLine, x: CGFloat, y: Int) {
    canvas.textPosition = CGPoint(x: x, y: CGFloat(height - y))
    CTLineDraw(line, canvas)
}
func label(_ value: String, _ x: CGFloat, _ y: Int, _ size: CGFloat, bold: Bool = false, right: Bool = false) {
    guard let line = lines(value, size: size, width: 790, bold: bold).first else { return }
    let edge = right ? x - CTLineGetTypographicBounds(line, nil, nil, nil) : x
    draw(line, x: edge, y: y)
}
func rule(_ y: Int) {
    canvas.setFillColor(pale)
    canvas.fill(CGRect(x: 52, y: height - y, width: 796, height: 2))
}

label("TableFlow", 52, 58, 32, bold: true)
label("Invoice " + receipt.number, 52, 106, 29)
var y = 173
for line in tableLines {
    draw(line, x: 52, y: y)
    y += 32
}
label(receipt.date, 52, y, 24)
y += 43
label("Item", 52, y, 22, bold: true)
label("Amount", 848, y, 22, bold: true, right: true)
rule(y + 12)
y += 46
for (index, item) in receipt.items.enumerated() {
    label(item.amount, 848, y, 24, right: true)
    for line in descriptions[index] {
        draw(line, x: 52, y: y)
        y += 32
    }
    rule(y - 13)
    y += 24
}
canvas.setFillColor(accent)
canvas.fill(CGRect(x: 52, y: height - y + 12, width: 796, height: 3))
label("Paid · " + receipt.payment, 52, y + 28, 27, bold: true)
label(receipt.total, 848, y + 28, 28, bold: true, right: true)
label(receipt.reward, 52, y + 65, 23)
label("Thank you for visiting!", 52, y + 105, 25, bold: true)

guard let image = canvas.makeImage(),
      let output = CFDataCreateMutable(nil, 0),
      let destination = CGImageDestinationCreateWithData(output, "public.png" as CFString, 1, nil)
else { exit(1) }
CGImageDestinationAddImage(destination, image, nil)
guard CGImageDestinationFinalize(destination) else { exit(1) }
FileHandle.standardOutput.write(output as Data)
