/**
 * 命令行参数处理的共用逻辑：
 * - --help / -h：打印用法并退出 0
 * - 未知标志：报错并退出 1
 * 
 * 在解析参数之前调用 validateArgsEarly() 来检查这些情况。
 */

/**
 * 在任何文件操作、浏览器启动或实际工作之前验证参数。
 * @param {string[]} argv - process.argv.slice(2)
 * @param {Set<string>} knownFlags - 所有已知标志的集合（包括 --help, -h）
 * @param {() => void} printUsage - 打印用法的函数
 */
export function validateArgsEarly(argv, knownFlags, printUsage) {
  // 检查 --help / -h
  if (argv.includes('--help') || argv.includes('-h')) {
    printUsage();
    process.exit(0);
  }
  
  // 检查未知标志
  for (const arg of argv) {
    if (arg.startsWith('--') || (arg.startsWith('-') && arg.length === 2 && arg !== '-')) {
      if (!knownFlags.has(arg)) {
        process.stderr.write(`✗ 未知标志: ${arg}\n\n`);
        printUsage();
        process.exit(1);
      }
    }
  }
}

/**
 * 打印用法的辅助函数。
 * @param {string} scriptName - 脚本名称
 * @param {string} usageText - 用法说明文本
 */
export function printUsageText(scriptName, usageText) {
  process.stderr.write(`用法: node ${scriptName} [选项]\n\n${usageText}\n`);
}
