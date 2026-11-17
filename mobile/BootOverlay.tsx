import { useEffect, useMemo, useRef } from "react";
import {
  Animated,
  Easing,
  Image,
  StyleSheet,
  Text,
  useColorScheme,
  useWindowDimensions,
  View,
} from "react-native";

type Mode = "boot" | "auth";

type Props = {
  mode?: Mode;
  visible?: boolean;
  onHidden?: () => void;
};

const SLOGAN = ["越", "来", "越", "懂", "你"];

/* 与 frontend/src/app/styles/tokens.css 的浅色一档对齐：
   paper=--step-0、tile=--tile-paper、ink=--ink、text2=--text-2；
   gold 取 #B07A1F —— 网页那边是金墨渐变，单色填充取两端之间才不会过亮。 */
const LIGHT = {
  paper: "#EDE4C8",
  tile: "#F5F0E1",
  ink: "#3D2F1A",
  text2: "#5A4C33",
  gold: "#B07A1F",
  line: "rgba(61,47,26,0.12)",
};

/* 深色只换纸与字；底座仍是 --tile-paper（#F5F0E1）跟着网页走 */
const DARK = {
  paper: "#1A1610",
  tile: "#F5F0E1",
  ink: "#EDE0C4",
  text2: "#C9B58A",
  gold: "#E8B54E",
  line: "rgba(237,224,196,0.14)",
};

export default function BootOverlay({ mode = "boot", visible = true, onHidden }: Props) {
  const dark = useColorScheme() === "dark";
  const pal = dark ? DARK : LIGHT;
  const { width, height } = useWindowDimensions();

  const logoSize = Math.max(96, Math.min(150, width * 0.26));
  const sloganSize = Math.max(24, Math.min(32, width * 0.072));
  const statusSize = Math.max(12, Math.min(15, width * 0.034));
  const lineWidth = Math.max(132, Math.min(260, width * 0.36));
  const sproutSize = Math.max(20, Math.min(36, width * 0.065));

  const fade = useRef(new Animated.Value(1)).current;
  const logoIn = useRef(new Animated.Value(0)).current;
  const lineIn = useRef(new Animated.Value(0)).current;
  const statusIn = useRef(new Animated.Value(0)).current;
  const sproutIn = useRef(new Animated.Value(0)).current;
  const underlineIn = useRef(new Animated.Value(0)).current;
  const bob = useRef(new Animated.Value(0)).current;
  const sway = useRef(new Animated.Value(0)).current;
  const sweep = useRef(new Animated.Value(0)).current;
  const chars = useRef(
    Array.from({ length: 8 }, () => new Animated.Value(0)),
  ).current;

  const loops = useRef<Animated.CompositeAnimation[]>([]);

  useEffect(() => {
    const auth = mode === "auth";
    const base = auth ? 60 : 180;
    const entrance: Animated.CompositeAnimation[] = [];

    chars.forEach((v, i) => {
      entrance.push(
        Animated.timing(v, {
          toValue: 1,
          duration: auth ? 300 : 420,
          delay: base + i * 55,
          easing: Easing.out(Easing.cubic),
          useNativeDriver: true,
        }),
      );
    });
    entrance.push(
      Animated.timing(logoIn, {
        toValue: 1,
        duration: auth ? 380 : 520,
        delay: auth ? 60 : 820,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
    );
    entrance.push(
      Animated.timing(underlineIn, {
        toValue: 1,
        duration: 360,
        delay: auth ? 200 : 380,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
    );
    entrance.push(
      Animated.timing(lineIn, {
        toValue: 1,
        duration: auth ? 420 : 550,
        delay: auth ? 120 : 1040,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
    );
    entrance.push(
      Animated.timing(statusIn, {
        toValue: 1,
        duration: 300,
        delay: auth ? 240 : 1180,
        useNativeDriver: true,
      }),
    );
    entrance.push(
      Animated.timing(sproutIn, {
        toValue: 1,
        duration: 400,
        delay: auth ? 280 : 1340,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
    );
    Animated.parallel(entrance).start();

    const bobLoop = Animated.loop(
      Animated.sequence([
        Animated.timing(bob, {
          toValue: 1,
          duration: 1800,
          easing: Easing.inOut(Easing.sin),
          useNativeDriver: true,
        }),
        Animated.timing(bob, {
          toValue: 0,
          duration: 1800,
          easing: Easing.inOut(Easing.sin),
          useNativeDriver: true,
        }),
      ]),
    );
    const swayLoop = Animated.loop(
      Animated.sequence([
        Animated.timing(sway, {
          toValue: 1,
          duration: 1800,
          easing: Easing.inOut(Easing.sin),
          useNativeDriver: true,
        }),
        Animated.timing(sway, {
          toValue: 0,
          duration: 1800,
          easing: Easing.inOut(Easing.sin),
          useNativeDriver: true,
        }),
      ]),
    );
    loops.current = [bobLoop, swayLoop];
    bobLoop.start();
    swayLoop.start();

    if (auth) {
      const sweepLoop = Animated.loop(
        Animated.timing(sweep, {
          toValue: 1,
          duration: 2200,
          easing: Easing.linear,
          useNativeDriver: true,
        }),
      );
      loops.current.push(sweepLoop);
      sweepLoop.start();
    }

    return () => {
      loops.current.forEach((l) => l.stop());
      loops.current = [];
    };
  }, [mode, chars, logoIn, lineIn, statusIn, sproutIn, underlineIn, bob, sway, sweep]);

  useEffect(() => {
    if (visible) return;
    Animated.timing(fade, {
      toValue: 0,
      duration: 320,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: true,
    }).start(({ finished }) => {
      if (finished) onHidden?.();
    });
  }, [visible, fade, onHidden]);

  const charY = useMemo(
    () => chars.map((v) => v.interpolate({ inputRange: [0, 1], outputRange: [6, 0] })),
    [chars],
  );
  const logoScale = logoIn.interpolate({ inputRange: [0, 1], outputRange: [0.9, 1] });
  const logoY = logoIn.interpolate({ inputRange: [0, 1], outputRange: [12, 0] });
  const bobY = bob.interpolate({ inputRange: [0, 1], outputRange: [0, -4] });
  const swayRotate = sway.interpolate({ inputRange: [0, 1], outputRange: ["-2.4deg", "2.4deg"] });
  const sweepX = sweep.interpolate({
    inputRange: [0, 1],
    outputRange: [-lineWidth * 0.35, lineWidth * 1.08],
  });

  const text = mode === "auth" ? "正在确认登录状态" : "正在打开知穗";

  return (
    <Animated.View
      style={[styles.root, { backgroundColor: pal.paper, opacity: fade }]}
      pointerEvents={visible ? "auto" : "none"}
      accessibilityElementsHidden={mode === "boot"}
      importantForAccessibility={mode === "boot" ? "no-hide-descendants" : "auto"}
    >
      <Image
        source={
          dark
            ? require("./assets/boot-glow-dark.png")
            : require("./assets/boot-glow-light.png")
        }
        style={{
          position: "absolute",
          width: width * 1.7,
          height: width * 1.7,
          top: -width * 0.55,
          left: -width * 0.35,
        }}
        resizeMode="cover"
        pointerEvents="none"
      />

      <View style={[styles.scene, { paddingHorizontal: 24 }]}>
        <Animated.View
          style={{
            opacity: logoIn,
            transform: [{ translateY: logoY }, { scale: logoScale }],
          }}
        >
          <Animated.View style={{ transform: [{ translateY: bobY }] }}>
            <View
              style={[
                styles.logoTile,
                {
                  width: logoSize,
                  height: logoSize,
                  /* 24% —— 与网页端 --r-icon 同一个比例，跟着尺寸缩放 */
                  borderRadius: logoSize * 0.24,
                  padding: logoSize * 0.08,
                  backgroundColor: pal.tile,
                },
              ]}
            >
              <Image
                source={require("./assets/splash-icon.png")}
                style={styles.logoImage}
                resizeMode="contain"
              />
            </View>
          </Animated.View>
        </Animated.View>

        <View style={[styles.sloganRow, { marginTop: Math.max(28, height * 0.044) }]}>
          <View style={styles.brand}>
            {["知", "穗"].map((ch, i) => (
              <Animated.Text
                key={ch}
                style={[
                  styles.sloganChar,
                  {
                    color: pal.gold,
                    fontSize: sloganSize,
                    opacity: chars[i],
                    transform: [{ translateY: charY[i] }],
                  },
                ]}
              >
                {ch}
              </Animated.Text>
            ))}
            <Animated.View
              style={[
                styles.brandUnderline,
                {
                  backgroundColor: pal.gold,
                  opacity: underlineIn,
                  transform: [{ scaleX: underlineIn }],
                },
              ]}
            />
          </View>
          <Animated.Text
            style={[
              styles.sloganChar,
              styles.comma,
              {
                color: pal.ink,
                fontSize: sloganSize,
                opacity: chars[2],
                transform: [{ translateY: charY[2] }],
              },
            ]}
          >
            ，
          </Animated.Text>
          {SLOGAN.map((ch, i) => (
            <Animated.Text
              key={`${ch}-${i}`}
              style={[
                styles.sloganChar,
                {
                  color: pal.ink,
                  fontSize: sloganSize,
                  opacity: chars[i + 3],
                  transform: [{ translateY: charY[i + 3] }],
                },
              ]}
            >
              {ch}
            </Animated.Text>
          ))}
        </View>

        <View style={[styles.lineWrap, { width: lineWidth, marginTop: Math.max(28, height * 0.04) }]}>
          <Animated.View style={[styles.line, { backgroundColor: pal.line, opacity: lineIn }]}>
            <Animated.View
              style={[
                styles.lineFill,
                { backgroundColor: pal.gold, transform: [{ scaleX: lineIn }] },
              ]}
            />
            {mode === "auth" ? (
              <Animated.View style={[styles.sweep, { transform: [{ translateX: sweepX }] }]} />
            ) : null}
          </Animated.View>

          <Animated.View
            style={[
              styles.sprout,
              {
                width: sproutSize,
                height: sproutSize * 0.9,
                opacity: sproutIn,
                transform: [{ scale: sproutIn }],
              },
            ]}
          >
            <Animated.View style={[styles.sproutInner, { transform: [{ rotate: swayRotate }] }]}>
              <View style={styles.stem} />
              <View style={[styles.leaf, styles.leafLeft]} />
              <View style={[styles.leaf, styles.leafRight]} />
            </Animated.View>
          </Animated.View>
        </View>

        <Animated.Text
          style={[
            styles.status,
            {
              color: pal.text2,
              fontSize: statusSize,
              marginTop: Math.max(14, height * 0.02),
              opacity: statusIn,
              transform: [
                {
                  translateY: statusIn.interpolate({
                    inputRange: [0, 1],
                    outputRange: [5, 0],
                  }),
                },
              ],
            },
          ]}
          accessibilityLiveRegion={mode === "auth" ? "polite" : "none"}
        >
          {text}
        </Animated.Text>
      </View>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  root: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    overflow: "hidden",
  },
  scene: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    width: "100%",
  },
  logoTile: {
    alignItems: "center",
    justifyContent: "center",
    shadowColor: "#503C1E",
    shadowOffset: { width: 0, height: 12 },
    shadowOpacity: 0.18,
    shadowRadius: 26,
    elevation: 10,
  },
  logoImage: {
    width: "100%",
    height: "100%",
  },
  sloganRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "center",
  },
  brand: {
    position: "relative",
    flexDirection: "row",
  },
  sloganChar: {
    fontWeight: "500",
    letterSpacing: 0.4,
  },
  comma: {
    marginLeft: -1,
    marginRight: -2,
  },
  brandUnderline: {
    position: "absolute",
    left: 2,
    right: 2,
    bottom: -2,
    height: 2,
    borderRadius: 2,
  },
  lineWrap: {
    height: 32,
    justifyContent: "flex-end",
  },
  line: {
    width: "100%",
    height: 3,
    borderRadius: 3,
    overflow: "hidden",
  },
  lineFill: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    borderRadius: 3,
  },
  sweep: {
    position: "absolute",
    top: 0,
    bottom: 0,
    left: 0,
    width: "26%",
    borderRadius: 3,
    backgroundColor: "rgba(255,248,226,0.88)",
  },
  sprout: {
    position: "absolute",
    right: -2,
    bottom: 1,
  },
  sproutInner: {
    width: "100%",
    height: "100%",
    transformOrigin: "50% 100%",
  },
  stem: {
    position: "absolute",
    left: "48%",
    bottom: 1,
    width: 2,
    height: "62%",
    borderRadius: 1,
    backgroundColor: "#7C9A54",
  },
  leaf: {
    position: "absolute",
    width: "46%",
    height: "34%",
    borderRadius: 999,
    borderWidth: 1,
    borderColor: "#5E7A3F",
  },
  leafLeft: {
    left: "4%",
    top: "28%",
    backgroundColor: "#7C9A54",
    transform: [{ rotate: "-33deg" }],
  },
  leafRight: {
    right: "4%",
    top: "4%",
    backgroundColor: "#8FAF62",
    transform: [{ rotate: "30deg" }],
  },
  status: {
    fontWeight: "400",
    letterSpacing: 0.3,
  },
});
