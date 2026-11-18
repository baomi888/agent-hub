import { StatusBar } from "expo-status-bar";
import * as Haptics from "expo-haptics";
import * as SplashScreen from "expo-splash-screen";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ComponentType, Ref } from "react";
import {
  ActivityIndicator,
  BackHandler,
  Linking,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import {
  APP_VERSION_LABEL,
  checkForUpdate,
  type AppUpdate,
} from "./version";
import {
  WebView as RawWebView,
  type WebViewProps,
  type WebViewNavigation,
} from "react-native-webview";
import { SafeAreaProvider } from "react-native-safe-area-context";

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

/** 版本清单的位置。跟着前台站点走，和 Web 一起部署（frontend/public/app-version.json）。 */
const VERSION_MANIFEST = `${SITE}/app-version.json`;

/* ── 启动屏：自己接管，不让系统一准备好就掀盖头 ─────────────────────
 *
 * iOS 的启动屏默认会淡出（setOptions 的 duration / fade），观感是「放完了再走」；
 * Android 12+ 的系统启动屏则是把 app 图标当 animated icon 播一段入场动画，
 * 只要 JS 首帧一画好就立刻退场 —— 在快机器上这段动画还没播完，看起来就是
 * 「闪一下进去了」。两边观感不一致，用户第一句话就是「安卓启动动画太短」。
 *
 * 做法：模块加载时先 preventAutoHide，等根视图真的排完版、并且至少过了
 * SPLASH_MIN_MS，才调 hide() 把控制权交回系统。此时底下已经画好了加载态
 * （同一张纸色），Android 12 是交叉淡出，iOS 走 450ms 淡出，都不跳帧。
 *
 * STARTED_AT 从模块求值算起，包含 JS 引擎初始化 + 取 bundle 的时间，
 * 所以「至少停留 1.6s」里已经含了真实冷启动耗时，不会额外拖慢。
 */
const SPLASH_MIN_MS = 1600;
/** 兜底：万一根视图迟迟不出 layout，也不能永远停在启动屏上。 */
const SPLASH_MAX_MS = 3200;
const STARTED_AT = Date.now();

/* 必须在首个渲染前调用。失败（例如热重载时已经隐藏过）不致命，吞掉即可。 */
SplashScreen.preventAutoHideAsync().catch(() => {});
/* 最后一道保险：万一根组件都没挂起来（渲染期抛错），AppInner 里的定时器不会执行，
 * 这里在 React 之外再兜一层，最迟 SPLASH_MAX_MS + 800ms 一定掀盖头，
 * 绝不把用户留在启动屏上。hide() 只是把一个 flag 置 false，重复调用无害。 */
setTimeout(() => SplashScreen.hide(), SPLASH_MAX_MS + 800);
/* 450ms 与系统默认的退场节奏接近；fade 目前只对 iOS 生效，安卓交给系统做交叉淡出。 */
SplashScreen.setOptions({ duration: 450, fade: true });

function AppInner() {
  const webRef = useRef<RawWebView>(null);
  const canGoBack = useRef(false);

  const [firstLoad, setFirstLoad] = useState(true);
  const [failed, setFailed] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  /* 根视图是否已经排完版：只有排完版，底下才真正画好了加载态，
     这时掀启动屏才不会露出白底。 */
  const [laidOut, setLaidOut] = useState(false);
  const [update, setUpdate] = useState<AppUpdate | null>(null);
  const splashHidden = useRef(false);
  const updateChecked = useRef(false);

  const hideSplash = useCallback(() => {
    if (splashHidden.current) return;
    splashHidden.current = true;
    SplashScreen.hide();
  }, []);

  /* 至少停留 SPLASH_MIN_MS 再交出启动屏。减去已经过去的冷启动时间，
     所以慢机器上等的是「还差多少」，不是再等满 1.6s。 */
  useEffect(() => {
    if (!laidOut) return;
    const wait = Math.max(0, SPLASH_MIN_MS - (Date.now() - STARTED_AT));
    const timer = setTimeout(hideSplash, wait);
    return () => clearTimeout(timer);
  }, [laidOut, hideSplash]);

  /* 兜底：layout 一直不来也不能卡死。 */
  useEffect(() => {
    const timer = setTimeout(hideSplash, SPLASH_MAX_MS);
    return () => clearTimeout(timer);
  }, [hideSplash]);

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
    setReloadKey((k) => k + 1);
  }, []);

  /* 首屏进来后再查版本：不跟启动抢网络和 JS 线程，也避免在加载页上盖弹窗。
     只在真的更新时 setUpdate —— 断网、404、清单写坏都静默。
     目前清单里指向的是安卓 APK，所以只在安卓上查。 */
  useEffect(() => {
    if (Platform.OS !== "android") return;
    if (firstLoad || failed || updateChecked.current) return;
    const timer = setTimeout(() => {
      updateChecked.current = true;
      checkForUpdate(VERSION_MANIFEST).then((info) => {
        if (info) setUpdate(info);
      });
    }, 1200);
    return () => clearTimeout(timer);
  }, [firstLoad, failed]);

  const openUpdate = useCallback(() => {
    if (!update) return;
    setUpdate(null);
    Linking.openURL(update.url).catch(() => {});
  }, [update]);

  return (
    <View style={styles.root} onLayout={() => setLaidOut(true)}>
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
        onLoadStart={() => setFailed(false)}
        onLoadEnd={() => setFirstLoad(false)}
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
            var s = document.createElement('style');
            s.textContent = 'html{-webkit-tap-highlight-color:transparent;}::-webkit-scrollbar{display:none;}';
            document.head && document.head.appendChild(s);
          })();
          true;
        `}
      />

      {/* 首屏加载态。盖在 WebView 上而不是替换它：
          替换会导致 WebView 重新挂载，加载完再"闪"一下。 */}
      {firstLoad && !failed && (
        <View style={styles.overlay} pointerEvents="none">
          <ActivityIndicator size="large" color="#C8892A" />
          <Text style={styles.overlayText}>正在打开知穗…</Text>
          <Text style={styles.versionTag}>{APP_VERSION_LABEL}</Text>
        </View>
      )}

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
          <Text style={styles.versionTag}>{APP_VERSION_LABEL}</Text>
        </View>
      )}

      {/* 版本提示。只在线上清单比当前包新时出现，所以正常使用永远看不到；
          用原生 Modal 是为了盖在 WebView 之上（安卓的 WebView 是独立 surface）。 */}
      <Modal
        visible={update !== null}
        transparent
        animationType="fade"
        statusBarTranslucent
        onRequestClose={() => setUpdate(null)}
      >
        <View style={styles.updateBackdrop}>
          <View style={styles.updateCard}>
            <Text style={styles.updateKicker}>发现新版本</Text>
            <Text style={styles.updateTitle}>知穗 {update ? `v${update.version}` : ""}</Text>
            {update?.notes ? <Text style={styles.updateNotes}>{update.notes}</Text> : null}
            <Text style={styles.updateMeta}>
              {update?.versionCode ? `新包版本号 ${update.versionCode} · ` : ""}
              当前 {APP_VERSION_LABEL}
            </Text>
            <Pressable style={styles.updatePrimary} onPress={openUpdate}>
              <Text style={styles.updatePrimaryText}>去更新</Text>
            </Pressable>
            <Pressable style={styles.updateGhost} onPress={() => setUpdate(null)}>
              <Text style={styles.updateGhostText}>以后再说</Text>
            </Pressable>
          </View>
        </View>
      </Modal>
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
  /* 版本小字压在最下面，不参与居中排布：加载页的视觉重心还是 spinner。 */
  versionTag: {
    position: "absolute",
    bottom: 44,
    fontSize: 12,
    letterSpacing: 0.3,
    color: "#8A7A5C",
  },

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

  updateBackdrop: {
    flex: 1,
    backgroundColor: "rgba(26, 22, 16, 0.45)",
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 28,
  },
  updateCard: {
    width: "100%",
    maxWidth: 360,
    backgroundColor: "#F6EED8",
    borderRadius: 22,
    paddingHorizontal: 24,
    paddingTop: 24,
    paddingBottom: 18,
    gap: 8,
  },
  updateKicker: { fontSize: 12, letterSpacing: 1, color: "#C8892A" },
  updateTitle: { fontSize: 22, fontWeight: "700", color: "#2A2418" },
  updateNotes: { fontSize: 14, lineHeight: 21, color: "#6B5B3E" },
  updateMeta: { fontSize: 12, color: "#8A7A5C", marginBottom: 6 },
  updatePrimary: {
    marginTop: 6,
    paddingVertical: 13,
    borderRadius: 999,
    backgroundColor: "#2A2418",
    alignItems: "center",
  },
  updatePrimaryText: { fontSize: 15, fontWeight: "600", color: "#F6EED8" },
  updateGhost: { paddingVertical: 10, alignItems: "center" },
  updateGhostText: { fontSize: 14, color: "#6B5B3E" },
});
