package main
import "fmt"
func main() {
	ch := make(chan int)
	close(ch)
	v, ok := <-ch
	fmt.Println(v, ok)
}
