"use client";

// 消息渲染错误边界：一条坏消息（如异常 markdown/HTML 结构）抛错时不白屏整个会话，
// 就地降级为可展开详情的警告卡
import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}
interface State {
  error: Error | null;
}

export default class BubbleErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div className="warn px-3 py-2 text-sm">
          <div className="font-semibold">⚠ 这条消息渲染出错了</div>
          <details className="mt-1">
            <summary className="cursor-pointer text-xs opacity-80">查看详情</summary>
            <div className="text-xs opacity-80">{String(this.state.error.message)}</div>
          </details>
        </div>
      );
    }
    return this.props.children;
  }
}
