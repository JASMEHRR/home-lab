import javax.imageio.ImageIO;
import java.awt.BasicStroke;
import java.awt.Color;
import java.awt.Font;
import java.awt.FontMetrics;
import java.awt.GradientPaint;
import java.awt.Graphics2D;
import java.awt.RenderingHints;
import java.awt.geom.Path2D;
import java.awt.geom.RoundRectangle2D;
import java.awt.image.BufferedImage;
import java.io.File;
import java.io.IOException;

/**
 * Draws the app's artwork (original shapes: a phone sending to a TV) so the repo
 * needs no binary files. Run by build.ps1 with: java tools/MakeArt.java <resDir> <assetsDir>
 */
public final class MakeArt {
    private static final Color TOP = new Color(0x2f, 0x6a, 0xe0);
    private static final Color BOTTOM = new Color(0x13, 0x1b, 0x3a);

    public static void main(String[] args) throws IOException {
        if (args.length != 2) {
            System.err.println("usage: MakeArt <resDir> <assetsDir>");
            System.exit(2);
        }
        File drawable = new File(args[0], "drawable-xhdpi");
        File assets = new File(args[1]);
        if (!drawable.isDirectory() && !drawable.mkdirs()) throw new IOException("cannot create " + drawable);
        if (!assets.isDirectory() && !assets.mkdirs()) throw new IOException("cannot create " + assets);

        write(banner(320, 180), new File(drawable, "banner.png"));
        write(icon(96), new File(drawable, "icon.png"));
        write(icon(192), new File(assets, "icon.png"));
    }

    private static void write(BufferedImage img, File f) throws IOException {
        if (!ImageIO.write(img, "png", f)) throw new IOException("no PNG writer for " + f);
    }

    private static Graphics2D paint(BufferedImage img) {
        Graphics2D g = img.createGraphics();
        g.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
        g.setRenderingHint(RenderingHints.KEY_TEXT_ANTIALIASING, RenderingHints.VALUE_TEXT_ANTIALIAS_ON);
        g.setRenderingHint(RenderingHints.KEY_STROKE_CONTROL, RenderingHints.VALUE_STROKE_PURE);
        return g;
    }

    private static BufferedImage banner(int w, int h) {
        BufferedImage img = new BufferedImage(w, h, BufferedImage.TYPE_INT_ARGB);
        Graphics2D g = paint(img);
        g.setPaint(new GradientPaint(0, 0, TOP, w, h, BOTTOM));
        g.fillRect(0, 0, w, h);
        glyph(g, 70, 18, 180, 92);
        g.setColor(Color.WHITE);
        g.setFont(new Font(Font.SANS_SERIF, Font.BOLD, 30));
        FontMetrics fm = g.getFontMetrics();
        String label = "Send to TV";
        g.drawString(label, (w - fm.stringWidth(label)) / 2, h - 30);
        g.dispose();
        return img;
    }

    private static BufferedImage icon(int size) {
        BufferedImage img = new BufferedImage(size, size, BufferedImage.TYPE_INT_ARGB);
        Graphics2D g = paint(img);
        g.setPaint(new GradientPaint(0, 0, TOP, size, size, BOTTOM));
        float r = size * 0.22f;
        g.fill(new RoundRectangle2D.Float(0, 0, size, size, r, r));
        float pad = size * 0.12f;
        glyph(g, pad, size * 0.24f, size - 2 * pad, size * 0.5f);
        g.dispose();
        return img;
    }

    /** Phone on the left, arrow, TV with a play triangle on the right, inside the given box. */
    private static void glyph(Graphics2D g, float x, float y, float w, float h) {
        float stroke = Math.max(2f, h * 0.07f);
        g.setStroke(new BasicStroke(stroke, BasicStroke.CAP_ROUND, BasicStroke.JOIN_ROUND));
        g.setColor(Color.WHITE);

        float pw = w * 0.2f, ph = h * 0.8f, px = x, py = y + (h - ph) / 2;
        g.draw(new RoundRectangle2D.Float(px, py, pw, ph, pw * 0.35f, pw * 0.35f));

        float ax0 = px + pw + w * 0.06f, ax1 = x + w * 0.46f, ay = y + h / 2;
        Path2D arrow = new Path2D.Float();
        arrow.moveTo(ax0, ay);
        arrow.lineTo(ax1, ay);
        arrow.moveTo(ax1 - h * 0.13f, ay - h * 0.13f);
        arrow.lineTo(ax1, ay);
        arrow.lineTo(ax1 - h * 0.13f, ay + h * 0.13f);
        g.draw(arrow);

        float tx = x + w * 0.52f, tw = w * 0.48f, th = h * 0.72f, ty = y + (h - th) / 2 - h * 0.06f;
        g.draw(new RoundRectangle2D.Float(tx, ty, tw, th, tw * 0.12f, tw * 0.12f));
        Path2D stand = new Path2D.Float();
        stand.moveTo(tx + tw * 0.32f, ty + th + h * 0.14f);
        stand.lineTo(tx + tw * 0.68f, ty + th + h * 0.14f);
        g.draw(stand);

        float cx = tx + tw / 2, cy = ty + th / 2, s = th * 0.24f;
        Path2D play = new Path2D.Float();
        play.moveTo(cx - s * 0.7f, cy - s);
        play.lineTo(cx + s, cy);
        play.lineTo(cx - s * 0.7f, cy + s);
        play.closePath();
        g.fill(play);
    }
}
