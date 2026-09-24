# typed: strict
# frozen_string_literal: true

# Homebrew formula for the codu command-line session browser.
class Codu < Formula
  desc "Browse and manage local Codex sessions in an ncdu-style TUI"
  homepage "https://github.com/ActivationEnergy/codu"
  url "https://raw.githubusercontent.com/ActivationEnergy/codu/v0.1.0/codu"
  version "0.1.0"
  sha256 "b2d111dbae4a9e48ae21055ff2df97ee813bdddaa5ea913119762f2fe86fbddc"
  license "MIT"

  depends_on "python@3.14"

  def install
    inreplace "codu", "#!/usr/bin/env python3", "#!#{formula_opt_bin("python@3.14")}/python3.14"
    bin.install "codu" => "codu"
  end

  test do
    assert_match "usage: codu", shell_output("#{bin}/codu --help")
  end
end
