import dev.latvian.mods.rhino.*;
import java.nio.file.*;
public class Check {
  public static void main(String[] a) throws Exception {
    for (String f : a) {
      String src = Files.readString(Path.of(f));
      Context cx = Check.ctx();
      CompilerEnvirons env = new CompilerEnvirons();
      try { new Parser(cx, env).parse(src, f, 1); System.out.println("OK " + f); }
      catch (Exception e) { System.out.println("ERR " + f + ": " + e); }
    }
  }
  static Context ctx() throws Exception {
    for (var c : Context.class.getDeclaredConstructors()) { c.setAccessible(true); if (c.getParameterCount()==0) return (Context)c.newInstance(); }
    return Context.enter();
  }
}
