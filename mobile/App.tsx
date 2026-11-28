import { StatusBar } from "expo-status-bar";
import * as Haptics from "expo-haptics";
import * as SplashScreen from "expo-splash-screen";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ComponentType, Ref } from "react";
import {
  BackHandler,
  Linking,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import {
  WebView as RawWebView,
  type WebViewProps,
  type WebViewNavigation,
} from "react-native-webview";
import { SafeAreaProvider } from "react-native-safe-area-context";
import BootOverlay from "./BootOverlay";

SplashScreen.preventAutoHideAsync().catch(() => {});
try {
  SplashScreen.setOptions({ fade: true, duration: 220 });
} catch {
  /* 老版本原生层不支持淡出时，保持默认即可 */
}

/**
 * 知穗 iOS / Android 外壳。
 *
 * ── 为什么是 WebView，而不是重写一遍 UI ───────────────────────────────
 * 团队只有 5 个人，Web 上已经有 14 个控制台页面 + 论坛（发图/举报/楼中楼）。
 * 重写一遍等于永久的双份维护：以后每加一个功能都要写两遍。壳里放线上站点，
 * 只有一套真相 —— Web 部署完，用户下次打开就是新版，不用发 App 版本。
 *
 * ── 为什么不打包静态资源 ───────────────────────────────────────────
 * 打包（WebView 指向本地文件）意味着每次 Web 更新都要重新发版 + 等审核，
 * 而 App Store 审核 1–2 天。对一个每周都在改的产品，那是自断手脚。
 * 代价是首屏必须联网（没网时只有错误页），这一条在内测阶段可接受。
 *
 * ── 安全区 ───────────────────────────────────────────────────────
 * WebView 全屏铺满（沉浸式）。页面侧靠 viewport-fit=cover + env(safe-area-inset-*)
 * 自己让出刘海与 Home 条 —— 这套是 2026-11-14 在 Web 侧补的，六个整屏容器都覆盖了。
 * 所以这里**不要**再给 WebView 加 paddingTop，否则会出现「刘海 + 网页自己让的」
 * 双重间距，顶部空出一大条。
 * 如果真机上某页顶部被压，说明那页的 Web 容器漏了安全区，去 Web 侧补；
 * 在这里加 padding 只会连带把其它页一起顶下去。
 */

/**
 * react-native-webview 的类型缺陷绕行。
 *
 * 它把组件声明成 `class WebView<P = undefined> extends Component<WebViewProps & P>`，
 * 而 `WebViewProps & undefined` 在 TS 里是 `never` —— 于是所有 props 都被判成 never，
 * 连回调参数都推断成 any。运行时完全不受影响，纯粹是类型声明写错了。
 * 显式断言回 WebViewProps 即可，不要为此去改 node_modules。
 */
const WebView = RawWebView as unknown as ComponentType<
  WebViewProps & { ref?: Ref<RawWebView> }
>;

const SITE = "https://myzhisui.cn";

/** 只有这些域名留在 App 内。其余（外链、帮助文档）交给系统浏览器。 */
const IN_APP_HOSTS = ["myzhisui.cn", "www.myzhisui.cn"];

function hostOf(url: string): string {
  const m = /^https?:\/\/([^/?#]+)/i.exec(url);
  return m ? m[1].toLowerCase() : "";
}

function AppInner() {
  const webRef = useRef<RawWebView>(null);
  const canGoBack = useRef(false);
  const loadedRef = useRef(false);
  const pendingDeepLink = useRef<string | null>(null);

  const [firstLoad, setFirstLoad] = useState(true);
  const [failed, setFailed] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [bootKey, setBootKey] = useState(0);
  const [minBootDone, setMinBootDone] = useState(false);
  const [bootHidden, setBootHidden] = useState(false);

  /* 原生启动层至少留 1.7s，避免页面加载太快时动画被拦腰截断。
     重试也重新走一遍，但不再要求完整时长 —— 重试时用新的 bootKey 重播。 */
  useEffect(() => {
    let alive = true;
    setMinBootDone(false);
    const t = setTimeout(() => {
      if (alive) setMinBootDone(true);
    }, 1700);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [bootKey]);

  /* 第一帧（含 BootOverlay）已经挂上，再把 expo 的原生启动图淡出。 */
  useEffect(() => {
    SplashScreen.hideAsync().catch(() => {});
  }, []);

  /* 安卓物理返回键：能后退就后退，退到底才交给系统退出。
     不处理的话，用户在任何页面按一下返回就直接退出 App —— 这是 WebView
     壳最典型的"手感不对"。iOS 没有这个键，靠侧滑手势和页面自己的返回按钮。 */
  useEffect(() => {
    if (Platform.OS !== "android") return;
    const sub = BackHandler.addEventListener("hardwareBackPress", () => {
      if (canGoBack.current) {
        webRef.current?.goBack();
        return true;
      }
      return false;
    });
    return () => sub.remove();
  }, []);

  /* Web → 壳：触觉反馈（2026-11-16，评审 P2-5）。
   *
   * 为什么是网页发消息、壳来给触感：切 tab 是 Next 的**客户端路由**，
   * 原生侧看不到任何导航事件；而 iOS 的 WKWebView 又没有 navigator.vibrate。
   * 所以只能反向：网页 `postMessage({t:"haptic"})` → 这里 → expo-haptics。
   * 对应实现见 `frontend/src/lib/haptics.ts`。
   *
   * 只认白名单里的 `t`，其余一律丢弃 —— WebView 里跑的是线上站点，
   * 它以后多发几种消息，不该让壳崩在这里。
   * 失败静默：省电模式、低电量模式下系统可能直接 reject，
   * 为一次触感弹错误框不值得。 */
  const onMessage = useCallback((e: { nativeEvent: { data: string } }) => {
    try {
      const msg = JSON.parse(e.nativeEvent.data) as { t?: string; kind?: string };
      if (msg?.t !== "haptic") return;
      if (msg.kind === "success") {
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
      } else {
        Haptics.selectionAsync().catch(() => {});
      }
    } catch {
      /* 不是 JSON、或者不是我们的消息：忽略 */
    }
  }, []);

  const retry = useCallback(() => {
    setFailed(false);
    setFirstLoad(true);
    setBootHidden(false);
    setBootKey((k) => k + 1);
    setReloadKey((k) => k + 1);
  }, []);

  /* 深链：支付宝授权完成后，服务端那张落地页会跳
   *   zhisui://oauth/alipay?state=<state>&r=ok
   * AndroidManifest 里 MainActivity 是 singleTask，所以这条深链会把 App 拉回
   * 前台并**复用原来的 Activity** —— WebView 还停在设置页，不会重建成首页。
   * 壳这一层的活只有一件：告诉页面「回来了」，让它的 /poll 立刻领结果。
   *
   * 为什么不能只靠 visibilitychange：部分机型从支付宝切回来不派发可见性变化，
   * 而深链是确定的信号。两套都留着，谁先到都能领（poll 幂等，重复也无害）。
   *
   * 冷启动（App 被系统回收后再由深链拉起）时 WebView 还没加载完，事件会丢，
   * 所以先存下来，等 onLoadEnd 后再注入两次（第一次可能早于 React hydrate）。
   */
  const dispatchDeepLink = useCallback((url: string) => {
    const script =
      `window.dispatchEvent(new CustomEvent("zhisui:oauth-return",{detail:${JSON.stringify(url)}}));true;`;
    webRef.current?.injectJavaScript(script);
  }, []);

  useEffect(() => {
    const onUrl = (event: { url: string }) => {
      if (!event.url.startsWith("zhisui://")) return;
      if (!loadedRef.current) {
        pendingDeepLink.current = event.url;
        return;
      }
      dispatchDeepLink(event.url);
    };
    const sub = Linking.addEventListener("url", onUrl);
    Linking.getInitialURL()
      .then((url) => {
        if (url && url.startsWith("zhisui://")) pendingDeepLink.current = url;
      })
      .catch(() => {});
    return () => sub.remove();
  }, [dispatchDeepLink]);

  const showBoot = !failed && (firstLoad || !minBootDone);

  return (
    <View style={styles.root}>
      {/* auto：跟随系统深浅色。配合 app.json 的 userInterfaceStyle: "automatic"，
          深色模式下状态栏自动转白字 —— 与 Web 侧的黑透状态栏是同一套观感。
          注意 expo-status-bar 没有 translucent（那是 RN 内置 StatusBar 的属性），
          沉浸式由 WebView 全屏布局 + 页面侧 safe-area 自己实现。 */}
      <StatusBar style="auto" />

      <WebView
        key={reloadKey}
        ref={webRef}
        source={{ uri: SITE }}
        style={styles.web}
        /* never：不要 WKWebView 自己加内边距。加了会和网页的 safe-area 叠加。 */
        contentInsetAdjustmentBehavior="never"
        /* 侧滑返回。iOS 上这是唯一"像 App"的手势，不开的话用户会觉得进了死胡同。 */
        allowsBackForwardNavigationGestures
        /* 下拉刷新：论坛、资料库都是长列表，用户会本能地往下拉。 */
        pullToRefreshEnabled
        /* 论坛发图走 <input type="file">。WKWebView 会调起系统相册/相机，
           需要在 app.json 的 infoPlist 里声明用途，否则**不是报错，是直接闪退**。 */
        allowFileAccess
        allowsInlineMediaPlayback
        onLoadStart={() => {
          loadedRef.current = false;
          setFailed(false);
        }}
        onLoadEnd={() => {
          setFirstLoad(false);
          loadedRef.current = true;
          // 冷启动时深链比页面先到：等文档加载完再注入，补两次以跨过 hydrate。
          const pending = pendingDeepLink.current;
          if (!pending) return;
          pendingDeepLink.current = null;
          setTimeout(() => dispatchDeepLink(pending), 600);
          setTimeout(() => dispatchDeepLink(pending), 2000);
        }}
        onError={() => {
          setFirstLoad(false);
          setFailed(true);
        }}
        onHttpError={(e) => {
          // 5xx 才当失败；4xx 交给页面自己渲染（站点自己的 404 页比一张错误图更好）
          const code = e.nativeEvent.statusCode ?? 0;
          if (code >= 500) {
            setFirstLoad(false);
            setFailed(true);
          }
        }}
        onNavigationStateChange={(s: WebViewNavigation) => {
          canGoBack.current = s.canGoBack;
        }}
        onMessage={onMessage}
        onShouldStartLoadWithRequest={(req) => {
          const { url } = req;
          // 自己的深链只由系统 intent 进来，绝不该在 WebView 里当页面加载；
          // 交回 Linking 会造成「自己唤起自己」的循环。
          if (url.startsWith("zhisui://")) return false;
          // tel: / mailto: / itms-apps: 之类，WebView 打不开，交给系统
          if (!/^https?:/i.test(url)) {
            Linking.openURL(url).catch(() => {});
            return false;
          }
          if (IN_APP_HOSTS.includes(hostOf(url))) return true;
          Linking.openURL(url).catch(() => {});
          return false;
        }}
        /* 注入一段极小的样式：去掉 iOS 点击时的灰色高亮矩形。
           不加的话点按钮会闪一块灰，很"网页"。
           ★ 同时打上 `__zhisuiApp` 标记：Web 侧的 isInstalled() 靠它判断
             「已经在 App 里，不要再劝我装到桌面」。WKWebView 既没有
             display-mode: standalone 也没有 navigator.standalone，
             不注入的话首页会弹出「把知穗装到桌面」——用户就在 App 里。 */
        injectedJavaScriptBeforeContentLoaded={`
          (function(){
            window.__zhisuiApp = true;
            if (document.documentElement) {
              document.documentElement.classList.add('in-app');
            }
            document.addEventListener('DOMContentLoaded', function(){
              if (document.documentElement) {
                document.documentElement.classList.add('in-app');
              }
            }, { once: true });
            var s = document.createElement('style');
            s.textContent = 'html{-webkit-tap-highlight-color:transparent;}::-webkit-scrollbar{display:none;}';
            document.head && document.head.appendChild(s);
          })();
          true;
        `}
      />

      {/* 错误态：给重试，而不是让用户看到一个白屏 App。 */}
      {failed && (
        <View style={styles.overlay}>
          <Text style={styles.errTitle}>连不上知穗</Text>
          <Text style={styles.errBody}>
            检查一下网络，或者稍后再试。{"\n"}
            如果连着校园网，试试切到流量。
          </Text>
          <Pressable style={styles.retry} onPress={retry}>
            <Text style={styles.retryText}>重新加载</Text>
          </Pressable>
        </View>
      )}

      {/* 首屏启动层。盖在 WebView 上而不是替换它：替换会导致 WebView
          重新挂载，加载完再"闪"一下。错误态先渲染在底下，启动层淡出后
          才露出来，不会在加载途中突然跳成错误页。 */}
      {!bootHidden && (
        <BootOverlay
          key={bootKey}
          visible={showBoot}
          onHidden={() => setBootHidden(true)}
        />
      )}
    </View>
  );
}

export default function App() {
  return (
    <SafeAreaProvider>
      <AppInner />
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#EDE4C8" },
  web: { flex: 1, backgroundColor: "#EDE4C8" },

  /* RN 0.88 去掉了 StyleSheet.absoluteFillObject，手写四边最稳 */
  overlay: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: "#EDE4C8",
    alignItems: "center",
    justifyContent: "center",
    gap: 14,
    paddingHorizontal: 32,
  },
  overlayText: { fontSize: 15, color: "#6B5B3E" },

  errTitle: { fontSize: 20, fontWeight: "600", color: "#2A2418" },
  errBody: { fontSize: 15, lineHeight: 23, color: "#6B5B3E", textAlign: "center" },
  retry: {
    marginTop: 8,
    paddingHorizontal: 26,
    paddingVertical: 12,
    borderRadius: 999,
    backgroundColor: "#2A2418",
  },
  retryText: { fontSize: 15, fontWeight: "600", color: "#F6EED8" },
});
