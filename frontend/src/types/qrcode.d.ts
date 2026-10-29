// `qrcode` 不自带类型声明，`@types/qrcode` 又要多装一个包。
// 我们只在打卡分享卡里用 `create()` 取矩阵，就只声明这一小块 ——
// 全量补一个手写声明反而容易和后端实现不符（比如 toDataURL 那一堆重载）。
//
// 用法：QRCode.create(url, { errorCorrectionLevel: "H" }) → { modules: { size, get(row,col) } }
declare module "qrcode" {
  interface BitMatrix {
    /** 边长（模块数），含定位图案，不含静区 */
    size: number;
    /** row 行 col 列是否为黑块。返回 0/1（库内部是 Uint8Array） */
    get(row: number, col: number): number;
  }
  interface QRCodeSymbol {
    modules: BitMatrix;
    version: number;
  }
  interface CreateOptions {
    errorCorrectionLevel?: "L" | "M" | "Q" | "H";
    version?: number;
    maskPattern?: number;
    toSJISFunc?: (code: string) => number;
  }
  const QRCode: {
    create(text: string, opts?: CreateOptions): QRCodeSymbol;
  };
  export default QRCode;
}
