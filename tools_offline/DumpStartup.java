import dev.latvian.mods.rhino.*;
import java.nio.file.*;
import java.util.*;
import java.util.regex.*;
import java.util.stream.*;

// 서버를 켜지 않고 KubeJS startup_scripts를 Rhino로 실행해서 전역 표(global.*)를 JSON으로 뽑는다.
// 사용: java -cp rhino.jar:. DumpStartup <startup_scripts 폴더> <prelude.js> <dump.js>  (결과는 표준 출력)
public class DumpStartup {
  public static void main(String[] a) throws Exception {
    Path dir = Path.of(a[0]);
    Context cx = ctx();
    Scriptable scope = cx.initStandardObjects();
    cx.evaluateString(scope, Files.readString(Path.of(a[1])), "prelude.js", 1, null);
    Pattern pri = Pattern.compile("priority\\s*:\\s*(-?\\d+)", Pattern.CASE_INSENSITIVE);
    List<Path> files;
    try (Stream<Path> s = Files.walk(dir)) { files = s.filter(p -> p.toString().endsWith(".js")).collect(Collectors.toList()); }
    Map<Path, Integer> prio = new HashMap<>();
    for (Path f : files) {
      String head = Files.readString(f).lines().limit(3).collect(Collectors.joining("\n"));
      Matcher m = pri.matcher(head);
      prio.put(f, m.find() ? Integer.parseInt(m.group(1)) : 0);
    }
    files.sort(Comparator.<Path>comparingInt(f -> -prio.get(f)).thenComparing(f -> dir.relativize(f).toString()));
    int ok = 0;
    for (Path f : files) {
      try { cx.evaluateString(scope, Files.readString(f), dir.relativize(f).toString(), 1, null); ok++; }
      catch (Throwable e) { System.err.println("skip " + dir.relativize(f) + ": " + String.valueOf(e).split("\n")[0]); }
    }
    System.err.println("ran " + ok + "/" + files.size());
    Object out = cx.evaluateString(scope, Files.readString(Path.of(a[2])), "dump.js", 1, null);
    System.out.println(out);
  }
  static Context ctx() throws Exception {
    for (var c : Context.class.getDeclaredConstructors()) { c.setAccessible(true); if (c.getParameterCount()==0) return (Context)c.newInstance(); }
    throw new RuntimeException("no Context()");
  }
}
