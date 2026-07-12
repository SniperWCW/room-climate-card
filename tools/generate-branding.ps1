Add-Type -AssemblyName System.Drawing

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot

$brandFiles = @(
    (Join-Path $repoRoot "icon.png"),
    (Join-Path $repoRoot "logo.png"),
    (Join-Path $repoRoot "banner.png"),
    (Join-Path $repoRoot "repository-open-graph.png"),
    (Join-Path $repoRoot "custom_components\room_climate\icon.png"),
    (Join-Path $repoRoot "custom_components\room_climate\icon@2x.png"),
    (Join-Path $repoRoot "custom_components\room_climate\logo.png"),
    (Join-Path $repoRoot "custom_components\room_climate\logo@2x.png")
)

foreach ($file in $brandFiles) {
    $dir = Split-Path -Parent $file
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir | Out-Null
    }
}

function New-Color {
    param(
        [int]$A,
        [int]$R,
        [int]$G,
        [int]$B
    )
    return [System.Drawing.Color]::FromArgb($A, $R, $G, $B)
}

function New-RoundRectPath {
    param(
        [float]$X,
        [float]$Y,
        [float]$Width,
        [float]$Height,
        [float]$Radius
    )

    $diameter = $Radius * 2
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $path.AddArc($X, $Y, $diameter, $diameter, 180, 90)
    $path.AddArc($X + $Width - $diameter, $Y, $diameter, $diameter, 270, 90)
    $path.AddArc($X + $Width - $diameter, $Y + $Height - $diameter, $diameter, $diameter, 0, 90)
    $path.AddArc($X, $Y + $Height - $diameter, $diameter, $diameter, 90, 90)
    $path.CloseFigure()
    return $path
}

function Add-Glow {
    param(
        [System.Drawing.Graphics]$Graphics,
        [float]$X,
        [float]$Y,
        [float]$Width,
        [float]$Height,
        [System.Drawing.Color]$Color
    )

    $brush = [System.Drawing.Drawing2D.PathGradientBrush]::new(
        [System.Drawing.PointF[]]@(
            [System.Drawing.PointF]::new($X, $Y + $Height / 2),
            [System.Drawing.PointF]::new($X + $Width / 2, $Y),
            [System.Drawing.PointF]::new($X + $Width, $Y + $Height / 2),
            [System.Drawing.PointF]::new($X + $Width / 2, $Y + $Height)
        )
    )
    $brush.CenterColor = $Color
    $brush.SurroundColors = [System.Drawing.Color[]]@([System.Drawing.Color]::FromArgb(0, $Color))
    $Graphics.FillEllipse($brush, $X, $Y, $Width, $Height)
    $brush.Dispose()
}

function Draw-ClimateMark {
    param(
        [System.Drawing.Graphics]$Graphics,
        [float]$X,
        [float]$Y,
        [float]$Size,
        [switch]$Tile,
        [switch]$TransparentBackground
    )

    $strokeWidth = [Math]::Max(6, $Size * 0.04)
    $shadowOffset = $Size * 0.03

    if ($Tile) {
        $shadowPath = New-RoundRectPath ($X + $shadowOffset) ($Y + $shadowOffset) $Size $Size ($Size * 0.22)
        $shadowBrush = New-Object System.Drawing.SolidBrush (New-Color 70 7 23 51)
        $Graphics.FillPath($shadowBrush, $shadowPath)
        $shadowBrush.Dispose()
        $shadowPath.Dispose()

        $tilePath = New-RoundRectPath $X $Y $Size $Size ($Size * 0.22)
        $tileBrush = [System.Drawing.Drawing2D.LinearGradientBrush]::new(
            [System.Drawing.PointF]::new($X, $Y),
            [System.Drawing.PointF]::new($X + $Size, $Y + $Size),
            (New-Color 255 16 53 99),
            (New-Color 255 11 166 153)
        )
        $Graphics.FillPath($tileBrush, $tilePath)
        $tileBrush.Dispose()

        Add-Glow -Graphics $Graphics -X ($X + $Size * 0.12) -Y ($Y + $Size * 0.08) -Width ($Size * 0.58) -Height ($Size * 0.58) -Color (New-Color 70 92 219 255)
        Add-Glow -Graphics $Graphics -X ($X + $Size * 0.46) -Y ($Y + $Size * 0.5) -Width ($Size * 0.42) -Height ($Size * 0.32) -Color (New-Color 90 255 166 77)

        $borderPen = New-Object System.Drawing.Pen (New-Color 70 255 255 255), ($Size * 0.01)
        $Graphics.DrawPath($borderPen, $tilePath)
        $borderPen.Dispose()
        $tilePath.Dispose()
    } elseif (-not $TransparentBackground) {
        $bgBrush = New-Object System.Drawing.SolidBrush (New-Color 255 18 50 92)
        $Graphics.FillEllipse($bgBrush, $X, $Y, $Size, $Size)
        $bgBrush.Dispose()
    }

    $housePen = New-Object System.Drawing.Pen ([System.Drawing.Color]::White, $strokeWidth)
    $housePen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $housePen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $housePen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round

    $houseLeft = $X + $Size * 0.2
    $houseTop = $Y + $Size * 0.26
    $houseWidth = $Size * 0.46
    $houseHeight = $Size * 0.42
    $roofPeakX = $houseLeft + $houseWidth * 0.5
    $roofPeakY = $Y + $Size * 0.1

    $Graphics.DrawLines($housePen, [System.Drawing.PointF[]]@(
        [System.Drawing.PointF]::new($houseLeft, $houseTop),
        [System.Drawing.PointF]::new($roofPeakX, $roofPeakY),
        [System.Drawing.PointF]::new($houseLeft + $houseWidth, $houseTop)
    ))
    $Graphics.DrawRectangle($housePen, $houseLeft, $houseTop, $houseWidth, $houseHeight)

    $windowBrush = New-Object System.Drawing.SolidBrush (New-Color 235 143 241 255)
    $windowRect = [System.Drawing.RectangleF]::new($houseLeft + $houseWidth * 0.18, $houseTop + $houseHeight * 0.18, $houseWidth * 0.2, $houseHeight * 0.2)
    $Graphics.FillRectangle($windowBrush, $windowRect)
    $windowBrush.Dispose()

    $thermoPen = New-Object System.Drawing.Pen (New-Color 255 255 171 82), ($strokeWidth * 0.85)
    $thermoPen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $thermoPen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round

    $thermoX = $houseLeft + $houseWidth * 0.76
    $thermoTop = $houseTop + $houseHeight * 0.08
    $thermoBottom = $houseTop + $houseHeight * 0.8
    $Graphics.DrawLine($thermoPen, $thermoX, $thermoTop, $thermoX, $thermoBottom)

    $thermoBrush = New-Object System.Drawing.SolidBrush (New-Color 255 255 171 82)
    $Graphics.FillEllipse($thermoBrush, $thermoX - $Size * 0.06, $thermoBottom - $Size * 0.04, $Size * 0.12, $Size * 0.12)
    $thermoBrush.Dispose()
    $thermoPen.Dispose()

    $airPen = New-Object System.Drawing.Pen (New-Color 230 188 248 255), ($strokeWidth * 0.62)
    $airPen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $airPen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $airPen.DashCap = [System.Drawing.Drawing2D.DashCap]::Round

    $arcRect1 = [System.Drawing.RectangleF]::new($X + $Size * 0.54, $Y + $Size * 0.2, $Size * 0.2, $Size * 0.22)
    $arcRect2 = [System.Drawing.RectangleF]::new($X + $Size * 0.58, $Y + $Size * 0.28, $Size * 0.17, $Size * 0.18)
    $Graphics.DrawArc($airPen, $arcRect1, 210, 120)
    $Graphics.DrawArc($airPen, $arcRect2, 205, 110)
    $airPen.Dispose()

    $dropBrush = New-Object System.Drawing.SolidBrush (New-Color 220 115 226 255)
    $dropPath = New-Object System.Drawing.Drawing2D.GraphicsPath
    $dropPath.AddBezier(
        $X + $Size * 0.36, $Y + $Size * 0.65,
        $X + $Size * 0.31, $Y + $Size * 0.57,
        $X + $Size * 0.28, $Y + $Size * 0.51,
        $X + $Size * 0.36, $Y + $Size * 0.45
    )
    $dropPath.AddBezier(
        $X + $Size * 0.36, $Y + $Size * 0.45,
        $X + $Size * 0.45, $Y + $Size * 0.51,
        $X + $Size * 0.42, $Y + $Size * 0.59,
        $X + $Size * 0.36, $Y + $Size * 0.65
    )
    $Graphics.FillPath($dropBrush, $dropPath)
    $dropBrush.Dispose()
    $dropPath.Dispose()

    $housePen.Dispose()
}

function Save-Bitmap {
    param(
        [string]$Path,
        [int]$Width,
        [int]$Height,
        [scriptblock]$Painter
    )

    $bitmap = New-Object System.Drawing.Bitmap $Width, $Height
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
    $graphics.Clear([System.Drawing.Color]::Transparent)

    & $Painter $graphics

    $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    $graphics.Dispose()
    $bitmap.Dispose()
}

function Draw-Wordmark {
    param(
        [System.Drawing.Graphics]$Graphics,
        [float]$Width,
        [float]$Height,
        [int]$TitleSize,
        [int]$SubtitleSize
    )

    $bgBrush = [System.Drawing.Drawing2D.LinearGradientBrush]::new(
        [System.Drawing.PointF]::new(0, 0),
        [System.Drawing.PointF]::new($Width, $Height),
        (New-Color 255 7 30 56),
        (New-Color 255 9 130 136)
    )
    $path = New-RoundRectPath 8 8 ($Width - 16) ($Height - 16) ($Height * 0.2)
    $Graphics.FillPath($bgBrush, $path)
    $bgBrush.Dispose()

    Add-Glow -Graphics $Graphics -X ($Width * 0.02) -Y ($Height * 0.08) -Width ($Width * 0.28) -Height ($Height * 0.72) -Color (New-Color 90 89 209 255)
    Add-Glow -Graphics $Graphics -X ($Width * 0.7) -Y ($Height * 0.15) -Width ($Width * 0.24) -Height ($Height * 0.55) -Color (New-Color 65 255 158 91)

    Draw-ClimateMark -Graphics $Graphics -X ($Width * 0.055) -Y ($Height * 0.14) -Size ($Height * 0.72) -Tile

    $titleFont = [System.Drawing.Font]::new("Segoe UI Semibold", $TitleSize, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
    $subtitleFont = [System.Drawing.Font]::new("Segoe UI", $SubtitleSize, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
    $titleBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::White)
    $subtitleBrush = New-Object System.Drawing.SolidBrush (New-Color 220 213 241 244)

    $Graphics.DrawString("Room Climate", $titleFont, $titleBrush, ($Width * 0.39), ($Height * 0.24))
    $Graphics.DrawString("Ventilation + cooling guidance", $subtitleFont, $subtitleBrush, ($Width * 0.39), ($Height * 0.56))

    $subtitleBrush.Dispose()
    $titleBrush.Dispose()
    $subtitleFont.Dispose()
    $titleFont.Dispose()
    $path.Dispose()
}

Save-Bitmap -Path (Join-Path $repoRoot "icon.png") -Width 256 -Height 256 -Painter {
    param($g)
    Draw-ClimateMark -Graphics $g -X 12 -Y 12 -Size 232 -Tile
}

Save-Bitmap -Path (Join-Path $repoRoot "logo.png") -Width 512 -Height 256 -Painter {
    param($g)
    Draw-Wordmark -Graphics $g -Width 512 -Height 256 -TitleSize 44 -SubtitleSize 18
}

Save-Bitmap -Path (Join-Path $repoRoot "custom_components\room_climate\icon.png") -Width 256 -Height 256 -Painter {
    param($g)
    Draw-ClimateMark -Graphics $g -X 12 -Y 12 -Size 232 -Tile
}

Save-Bitmap -Path (Join-Path $repoRoot "custom_components\room_climate\icon@2x.png") -Width 512 -Height 512 -Painter {
    param($g)
    Draw-ClimateMark -Graphics $g -X 24 -Y 24 -Size 464 -Tile
}

Save-Bitmap -Path (Join-Path $repoRoot "custom_components\room_climate\logo.png") -Width 512 -Height 256 -Painter {
    param($g)
    Draw-Wordmark -Graphics $g -Width 512 -Height 256 -TitleSize 44 -SubtitleSize 18
}

Save-Bitmap -Path (Join-Path $repoRoot "custom_components\room_climate\logo@2x.png") -Width 1024 -Height 512 -Painter {
    param($g)
    Draw-Wordmark -Graphics $g -Width 1024 -Height 512 -TitleSize 88 -SubtitleSize 36
}

$bannerPainter = {
    param($g)

    $bgRect = [System.Drawing.RectangleF]::new(0, 0, 1280, 640)
    $bgBrush = [System.Drawing.Drawing2D.LinearGradientBrush]::new(
        [System.Drawing.PointF]::new(0, 0),
        [System.Drawing.PointF]::new(1280, 640),
        (New-Color 255 7 26 48),
        (New-Color 255 8 109 124)
    )
    $g.FillRectangle($bgBrush, $bgRect)
    $bgBrush.Dispose()

    Add-Glow -Graphics $g -X 40 -Y 10 -Width 440 -Height 440 -Color (New-Color 110 64 196 255)
    Add-Glow -Graphics $g -X 830 -Y 80 -Width 360 -Height 260 -Color (New-Color 85 255 172 87)
    Add-Glow -Graphics $g -X 980 -Y 360 -Width 240 -Height 180 -Color (New-Color 90 255 127 80)

    $wavePen = New-Object System.Drawing.Pen (New-Color 40 255 255 255), 6
    $wavePen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $wavePen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $g.DrawBezier($wavePen, 720, 445, 855, 380, 1010, 510, 1180, 438)
    $g.DrawBezier($wavePen, 700, 500, 835, 435, 980, 565, 1145, 498)
    $wavePen.Dispose()

    Draw-ClimateMark -Graphics $g -X 88 -Y 98 -Size 290 -Tile

    $titleBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::White)
    $accentBrush = New-Object System.Drawing.SolidBrush (New-Color 255 181 243 255)
    $subtitleBrush = New-Object System.Drawing.SolidBrush (New-Color 220 223 241 245)

    $titleFont = [System.Drawing.Font]::new("Segoe UI Semibold", 60, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
    $subtitleFont = [System.Drawing.Font]::new("Segoe UI", 24, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
    $pillFont = [System.Drawing.Font]::new("Segoe UI Semibold", 19, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)

    $g.DrawString("Room Climate", $titleFont, $titleBrush, 430, 144)
    $g.DrawString("Ventilation, cooling and room comfort guidance for Home Assistant", $subtitleFont, $subtitleBrush, 434, 226)

    $pillData = @(
        @{ X = 434; Y = 320; Width = 194; Label = "Ventilation"; Fill = (New-Color 70 104 232 255); Foreground = [System.Drawing.Color]::White },
        @{ X = 648; Y = 320; Width = 150; Label = "Cooling"; Fill = (New-Color 75 104 255 202); Foreground = [System.Drawing.Color]::White },
        @{ X = 818; Y = 320; Width = 162; Label = "Comfort"; Fill = (New-Color 85 255 171 82); Foreground = (New-Color 255 20 46 74) }
    )

    foreach ($pill in $pillData) {
        $pillPath = New-RoundRectPath $pill.X $pill.Y $pill.Width 52 26
        $pillBrush = New-Object System.Drawing.SolidBrush $pill.Fill
        $pillTextBrush = New-Object System.Drawing.SolidBrush $pill.Foreground
        $g.FillPath($pillBrush, $pillPath)
        $g.DrawString($pill.Label, $pillFont, $pillTextBrush, $pill.X + 22, $pill.Y + 13)
        $pillTextBrush.Dispose()
        $pillBrush.Dispose()
        $pillPath.Dispose()
    }

    $g.DrawString("Custom integration + bundled Lovelace card", $subtitleFont, $accentBrush, 434, 408)

    $titleBrush.Dispose()
    $accentBrush.Dispose()
    $subtitleBrush.Dispose()
    $titleFont.Dispose()
    $subtitleFont.Dispose()
    $pillFont.Dispose()
}

Save-Bitmap -Path (Join-Path $repoRoot "banner.png") -Width 1280 -Height 640 -Painter $bannerPainter
Save-Bitmap -Path (Join-Path $repoRoot "repository-open-graph.png") -Width 1280 -Height 640 -Painter $bannerPainter
